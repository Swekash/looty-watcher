# looty-watcher

Telegram deal watcher bot for Looty.in.
Monitors source channels, sends approval cards, writes approved deals to GSheet.

## How it works

1. Telethon (logged in as Swetha's account) watches:
   - @dealsmagnet
   - @Loot_DealsX
   - @amazinglootsdealsoffers

2. Each new deal post is parsed → Amazon URL extracted → converted to Looty affiliate link

3. A formatted card is sent to Swetha's Telegram via @LootyWatcherbot with ✅ / ❌ buttons

4. On Approve → deal row appended to GSheet Inbox with TRUE under Approved column

## Railway Environment Variables

Set these in Railway → looty-watcher → Variables:

| Variable | Value |
|---|---|
| `TELETHON_API_ID` | 39037715 |
| `TELETHON_API_HASH` | 807a875e1ce0f65fad3feb85a1151afe |
| `TELETHON_SESSION` | (base64 session string) |
| `BOT_TOKEN` | 8936124221:AAFzC85Mzdt5cxRJRSIixkT4BbFBlMcdcBE |
| `TELEGRAM_CHAT_ID` | 8494846418 |
| `AMAZON_PARTNER_TAG` | looty08-21 |
| `GOOGLE_CREDS_JSON_CONTENT` | (full service account JSON) |

## Deploy Steps

1. Create new GitHub repo: `Swekash/looty-watcher`
2. Push these files
3. New Railway service → connect repo
4. Set all env vars above
5. Deploy → check logs for "Telethon client started ✅" and "Bot polling started ✅"
