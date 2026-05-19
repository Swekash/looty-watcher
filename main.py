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
TARGET_SHEET = "Other_Gadgets"

SOURCE_CHANNELS = [
    "dealsmagnet",
    "Loot_DealsX",
    "amazinglootsdealsoffers",
]

# ── GOOGLE SHEETS ─────────────────────────────────────────────────────────────
# Column order in Other_Gadgets (row 1):
# Approved | Name | ShortDesc | ImageURL | AmazonLink | VideoLink | Categories | Updated | Price | MRP | Trending | SourceURL

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GSHEET_ID)
    return sh.worksheet(TARGET_SHEET)


def append_to_sheet(deal: dict):
    ws = get_sheet()
    row = [
        "TRUE",                                         # Approved
        deal.get("title", ""),                          # Name
        deal.get("short_desc", ""),                     # ShortDesc
        deal.get("image_url", ""),                      # ImageURL
        deal.get("looty_url", ""),                      # AmazonLink
        "",                                             # VideoLink
        "Other_Gadgets",                                # Categories
        datetime.now().strftime("%Y-%m-%d %H:%M"),      # Updated
        deal.get("price", ""),                          # Price
        deal.get("original_price", ""),                 # MRP
        "FALSE",                                        # Trending
        deal.get("source_url", ""),                     # SourceURL
    ]
    ws.append_row(row)
    logger.info(f"✅ Appended to {TARGET_SHEET}: {deal.get('title', '')[:60]}")


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
    pattern = r"https?://(?:www\.)?(?:amazon\.in|amzn\.in|amzn\.to)[^\s\])]+"
    m = re.search(pattern, text)
    return m.group(0) if m else None


def build_looty_url(asin: str) -> str:
    return f"https://www.amazon.in/dp/{asin}/?tag={PARTNER_TAG}"


def extract_price_info(text: str) -> dict:
    info = {"price": "", "original_price": "", "discount": ""}
    price_matches = re.findall(r"(?:₹|Rs\.?|INR)\s*([\d,]+)", text)
    if len(price_matches) >= 2:
        prices = [int(p.replace(",", "")) for p in price_matches]
        info["price"] = str(min(prices))
        info["original_price"] = str(max(prices))
    elif len(price_matches) == 1:
        info["price"] = str(int(price_matches[0].replace(",", "")))
    disc = re.search(r"(\d+)\s*%\s*off", text, re.IGNORECASE)
    if disc:
        info["discount"] = f"{disc.group(1)}% off"
    return info


def parse_deal(message_text: str, source_channel: str) -> dict | None:
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

    title = ""
    for line in message_text.splitlines():
        line = line.strip()
        if line and not line.startswith("http") and len(line) > 5:
            cleaned = re.sub(r"^[\U0001F300-\U0001FFFF\s🔥💥🎯⚡🛒🏷️👉✅❗•\-–—]+", "", line).strip()
            if cleaned and len(cleaned) > 4:
                title = cleaned[:120]
                break
    if not title:
        title = f"Deal from @{source_channel}"

    short_desc = ""
    if price_info["price"]:
        short_desc = f"₹{price_info['price']}"
        if price_info["original_price"]:
            short_desc += f" (MRP ₹{price_info['original_price']})"
        if price_info["discount"]:
            short_desc += f" • {price_info['discount']}"

    return {
        "title": title,
        "short_desc": short_desc,
        "price": price_info["price"],
        "original_price": price_info["original_price"],
        "discount": price_info["discount"],
        "looty_url": looty_url,
        "source": f"@{source_channel}",
        "source_url": f"https://t.me/{source_channel}",
        "posted_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "asin": asin,
        "image_url": "",
    }


def format_card(deal: dict) -> str:
    lines = [f"🏷️ *{deal['title']}*", ""]
    if deal["price"]:
        price_line = f"💰 *₹{deal['price']}*"
        if deal["original_price"]:
            price_line += f"  ~~₹{deal['original_price']}~~"
        if deal["discount"]:
            price_line += f"  `{deal['discount']}`"
        lines.append(price_line)
    lines.append(f"🔗 [View on Amazon]({deal['looty_url']})")
    lines.append("")
    lines.append(f"📢 {deal['source']}  •  🕐 {deal['posted_at']}")
    return "\n".join(lines)


# ── PENDING DEALS STORE (in-memory) ──────────────────────────────────────────
pending: dict[str, dict] = {}


# ── TELEGRAM BOT HANDLERS ─────────────────────────────────────────────────────
async def handle_callback(update, context):
    query = update.callback_query
    await query.answer()
    action, key = query.data.split(":", 1)
    deal = pending.get(key)

    if not deal:
        try:
            original = query.message.caption or query.message.text or ""
            await query.edit_message_caption(
                caption=original + "\n\n⚠️ _Deal expired. Restart bot._",
                parse_mode="Markdown",
                reply_markup=None,
            )
        except Exception:
            await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "approve":
        try:
            append_to_sheet(deal)
            result_text = "✅ *Approved & saved to Other\\_Gadgets!*"
        except Exception as e:
            logger.error(f"GSheet error: {e}")
            result_text = f"⚠️ Approved but GSheet error:\n`{e}`"
        pending.pop(key, None)
    else:
        result_text = "❌ *Disapproved.*"
        pending.pop(key, None)

    try:
        original = query.message.caption or query.message.text or ""
        await query.edit_message_caption(
            caption=original + f"\n\n{result_text}",
            parse_mode="Markdown",
            reply_markup=None,
        )
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass


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
                logger.info(f"No Amazon deal in message from @{source}")
                return

            key = f"{source}_{msg.id}_{int(time.time())}"
            pending[key] = deal

            card_text = format_card(deal)
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{key}"),
                InlineKeyboardButton("❌ Disapprove", callback_data=f"disapprove:{key}"),
            ]])

            if msg.photo:
                photo_bytes = await client.download_media(msg.photo, bytes)
                await bot.send_photo(
                    chat_id=CHAT_ID,
                    photo=photo_bytes,
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
            logger.info(f"Card sent: {deal['title'][:60]}")

        except Exception as e:
            logger.error(f"Handler error: {e}", exc_info=True)

    await client.run_until_disconnected()


# ── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_callback))

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot polling started ✅")
        await run_watcher(app.bot)
        await app.updater.stop()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
