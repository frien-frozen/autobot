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

## Deploy / keep Free Render awake (no paid plan)

Free Render sleeps after ~15 min idle. While asleep, reminders won’t fire.

**Hack:** ping your health URL every 5–10 minutes from a free uptime/cron service.

1. Deploy on **Free**
2. Copy your URL, e.g. `https://instabot-xxxx.onrender.com/health`
3. Create a job at [cron-job.org](https://cron-job.org) or [UptimeRobot](https://uptimerobot.com):
   - Method: `GET`
   - URL: `https://YOUR-SERVICE.onrender.com/health`
   - Interval: every **5 minutes**
4. Confirm logs show regular requests and the process stays up

Limits: Free still has monthly hours (~750). If Render changes sleep rules, pings may stop working — then use Starter / a VPS.

Env vars: `BOT_TOKEN`, `OWNER_USER_ID`, `OWNER_IDS`, `GEMINI_API_KEY`  
(`MODE=webhook` — set manually or via `render.yaml`)

## Notes

- Public auto-replies only see non-secret memories
- Truncation: replies use higher token limits + message splitting
- Business bots usually only reply in chats active in the last ~24h
- Outbound `text @user` works best after that user has messaged you at least once (chat id known)
