import asyncio
import base64
import json
import logging
import os
import re
import time
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── ENV ──────────────────────────────────────────────────────────────────────
API_ID = int(os.environ["TELETHON_API_ID"])
API_HASH = os.environ["TELETHON_API_HASH"]
SESSION_B64 = os.environ["TELETHON_SESSION"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON_CONTENT"]
PARTNER_TAG = os.getenv("AMAZON_PARTNER_TAG", "looty08-21")
GSHEET_ID = "1hTQD9sx5NTPTRXCYK2mhrkq8V2_wGFBDK55wiLYRJsU"

SOURCE_CHANNELS = [
    "dealsmagnet",
    "Loot_DealsX",
    "amazinglootsdealsoffers",
]

# ── GOOGLE SHEETS ─────────────────────────────────────────────────────────────
def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GSHEET_ID)
    # Use first sheet (Inbox)
    return sh.get_worksheet(0)


def append_to_sheet(deal: dict):
    ws = get_sheet()
    headers = ws.row_values(1)
    if not headers:
        # Create headers if sheet is empty
        headers = ["Title", "Price", "Original Price", "Discount", "Looty URL", "Source", "Posted At", "Approved"]
        ws.append_row(headers)

    row = []
    for h in headers:
        key = h.strip().lower().replace(" ", "_")
        row.append(deal.get(key, ""))
    ws.append_row(row)
    logger.info(f"Appended to sheet: {deal.get('title', '')}")


# ── AMAZON LINK UTILS ─────────────────────────────────────────────────────────
def extract_asin(text: str) -> str | None:
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"asin=([A-Z0-9]{10})",
        r"/([A-Z0-9]{10})(?:/|\?|$)",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return None


def extract_amazon_url(text: str) -> str | None:
    pattern = r"https?://(?:www\.)?(?:amazon\.in|amzn\.in|amzn\.to|dl\.flipkart\.com|fkrt\.it)[^\s\])]+"
    m = re.search(pattern, text)
    return m.group(0) if m else None


def build_looty_url(asin: str) -> str:
    return f"https://www.amazon.in/dp/{asin}/?tag={PARTNER_TAG}"


def extract_price_info(text: str) -> dict:
    """Extract price details from deal message text."""
    info = {"price": "", "original_price": "", "discount": ""}

    # Price patterns like ₹999, Rs.999, INR 999
    price_matches = re.findall(r"(?:₹|Rs\.?|INR)\s*([\d,]+)", text)
    if len(price_matches) >= 2:
        prices = [int(p.replace(",", "")) for p in price_matches]
        info["price"] = f"₹{min(prices):,}"
        info["original_price"] = f"₹{max(prices):,}"
    elif len(price_matches) == 1:
        info["price"] = f"₹{int(price_matches[0].replace(',', '')):,}"

    # Discount pattern like 80% off
    disc = re.search(r"(\d+)\s*%\s*off", text, re.IGNORECASE)
    if disc:
        info["discount"] = f"{disc.group(1)}%"

    return info


def parse_deal(message_text: str, source_channel: str) -> dict | None:
    """Parse a Telegram message into a deal dict."""
    if not message_text:
        return None

    url = extract_amazon_url(message_text)
    if not url:
        return None

    asin = extract_asin(url)
    if not asin:
        return None

    looty_url = build_looty_url(asin)
    price_info = extract_price_info(message_text)

    # Title: first non-empty line that isn't a URL/emoji-only
    title = ""
    for line in message_text.splitlines():
        line = line.strip()
        if line and not line.startswith("http") and len(line) > 5:
            # Strip leading emoji/symbols
            cleaned = re.sub(r"^[\U0001F300-\U0001FFFF\s🔥💥🎯⚡🛒🏷️👉✅❗•\-–—]+", "", line).strip()
            if cleaned and len(cleaned) > 4:
                title = cleaned[:120]
                break

    if not title:
        title = f"Deal from @{source_channel}"

    return {
        "title": title,
        "price": price_info["price"],
        "original_price": price_info["original_price"],
        "discount": price_info["discount"],
        "looty_url": looty_url,
        "source": f"@{source_channel}",
        "posted_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "approved": "",
        "asin": asin,
        "raw_url": url,
    }


def format_card(deal: dict) -> str:
    lines = []
    lines.append(f"🏷️ *{deal['title']}*")
    lines.append("")
    if deal["price"]:
        price_line = f"💰 *{deal['price']}*"
        if deal["original_price"]:
            price_line += f"  ~~{deal['original_price']}~~"
        if deal["discount"]:
            price_line += f"  `{deal['discount']} off`"
        lines.append(price_line)
    lines.append(f"🔗 [View on Amazon]({deal['looty_url']})")
    lines.append("")
    lines.append(f"📢 {deal['source']}  •  🕐 {deal['posted_at']}")
    return "\n".join(lines)


# ── PENDING DEALS STORE (in-memory) ──────────────────────────────────────────
pending: dict[str, dict] = {}  # callback_data_key -> deal dict


# ── TELEGRAM BOT HANDLERS ─────────────────────────────────────────────────────
async def handle_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data  # "approve:KEY" or "disapprove:KEY"

    action, key = data.split(":", 1)
    deal = pending.get(key)

    if not deal:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_caption(
            caption=query.message.caption + "\n\n⚠️ _Deal data expired._",
            parse_mode="Markdown",
        )
        return

    if action == "approve":
        deal["approved"] = "TRUE"
        try:
            append_to_sheet(deal)
            result_text = "✅ *Approved & added to GSheet!*"
        except Exception as e:
            logger.error(f"GSheet error: {e}")
            result_text = f"✅ Approved but GSheet error: {e}"
        pending.pop(key, None)
    else:
        result_text = "❌ *Disapproved.*"
        pending.pop(key, None)

    # Edit message to remove buttons and show result
    try:
        original = query.message.caption or query.message.text or ""
        await query.edit_message_caption(
            caption=original + f"\n\n{result_text}",
            parse_mode="Markdown",
            reply_markup=None,
        )
    except Exception:
        await query.edit_message_reply_markup(reply_markup=None)


# ── TELETHON WATCHER ──────────────────────────────────────────────────────────
async def run_watcher(bot: Bot):
    session_str = base64.b64decode(SESSION_B64).decode()
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await client.start()
    logger.info("Telethon client started ✅")

    @client.on(events.NewMessage(chats=SOURCE_CHANNELS))
    async def handler(event):
        try:
            msg = event.message
            text = msg.text or msg.caption or ""
            source = event.chat.username or str(event.chat_id)

            deal = parse_deal(text, source)
            if not deal:
                logger.info(f"No deal found in message from @{source}")
                return

            key = f"{source}_{msg.id}_{int(time.time())}"
            pending[key] = deal

            card_text = format_card(deal)
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve:{key}"),
                    InlineKeyboardButton("❌ Disapprove", callback_data=f"disapprove:{key}"),
                ]
            ])

            # Send with photo if available
            if msg.photo:
                photo = await client.download_media(msg.photo, bytes)
                await bot.send_photo(
                    chat_id=CHAT_ID,
                    photo=photo,
                    caption=card_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            else:
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=card_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                    disable_web_page_preview=False,
                )

            logger.info(f"Deal card sent: {deal['title'][:50]}")

        except Exception as e:
            logger.error(f"Handler error: {e}", exc_info=True)

    await client.run_until_disconnected()


# ── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    # Build the PTB application
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_callback))

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot polling started ✅")

        bot = app.bot
        # Run Telethon watcher alongside
        await run_watcher(bot)

        await app.updater.stop()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
