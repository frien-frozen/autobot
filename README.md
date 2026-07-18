# chatauto

Telegram Business bot that:

1. Answers strangers **as you**
2. Acts as your **private assistant** when you message yourself / DM the bot
3. Remembers facts (including secrets)
4. Sends reminders + scheduled / recurring texts

## Features

| You say | What happens |
|---------|----------------|
| `remind me tomorrow 10am to ask dad for money` | Reminds you, then later asks if you actually did it |
| `text @someone: hey, free later?` | Sends as you (Business connection) |
| `every monday 9am text @someone: weekly check-in` | Recurring outbound |
| `remember: shipping baxtiyorov.uz redesign` | Saved to memory, used in future chats |
| `don't tell anyone: ...` | Saved as **secret** — never used when talking to other people |

Owner accounts are configured via `OWNER_USER_ID` + `OWNER_IDS`.

## What you need

1. Telegram Premium + Business features
2. Bot with **Business Mode** enabled in BotFather
3. Gemini API key
4. Hosting that stays **awake 24/7** if you want reminders (see below)

## Configure

```bash
cp .env.example .env
# set BOT_TOKEN, OWNER_USER_ID, OWNER_IDS, GEMINI_API_KEY
pip install -r requirements.txt
python -m chatauto
```

`MODE=polling` for local. `MODE=webhook` on Render.

## Owner mode

Message **@bakhy_autobot** directly, or message yourself from another of your accounts.

`/start` prints examples.

## Deploy / Render warning

**Free Render sleeps** after idle time. While asleep:

- webhooks delay / miss
- reminders and scheduled texts **do not fire**

For a real assistant, use one of:

- Render **Starter** (always on) + a persistent Disk for SQLite
- Railway / Fly.io / any small VPS

Env vars to set on host: `BOT_TOKEN`, `OWNER_USER_ID`, `OWNER_IDS`, `GEMINI_API_KEY`  
(`MODE=webhook` is already in `render.yaml`)

## Notes

- Public auto-replies only see non-secret memories
- Truncation: replies use higher token limits + message splitting
- Business bots usually only reply in chats active in the last ~24h
- Outbound `text @user` works best after that user has messaged you at least once (chat id known)
