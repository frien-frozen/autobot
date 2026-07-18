# chatauto

Telegram Business (Secretary Mode) bot that answers your **private chats as you**, using Gemini, with per-chat memory and basic contact profile context.

## What you need

1. Telegram Premium + Business features
2. A bot from [@BotFather](https://t.me/BotFather) with **Business Mode** enabled
3. Gemini API key
4. A [Render](https://render.com) account

## 1. Create & connect the bot (Telegram)

1. Message BotFather → `/newbot` → copy the token
2. Open bot settings in BotFather → enable **Business Mode** / Secretary Mode
3. On your phone: **Settings → Business → Chatbots** → add `@your_bot`
4. Permissions: allow reading + replying
5. Chats: choose **All** (as you wanted)
6. Get your numeric user id from [@userinfobot](https://t.me/userinfobot)

## 2. Configure locally

```bash
cd chatauto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

- `BOT_TOKEN`
- `OWNER_USER_ID`
- `GEMINI_API_KEY`
- `WEBHOOK_URL` — local: ngrok HTTPS URL; on Render: leave empty (uses `RENDER_EXTERNAL_URL`)
- `WEBHOOK_SECRET` — long random string (letters, numbers, `_`, `-` only)

Edit `persona.txt` so replies sound like you (paste a few real message examples in your style).

## 3. Deploy on Render

### Option A — Blueprint

1. Push this repo to GitHub
2. Render → **New → Blueprint** → select the repo (`render.yaml`)
3. Fill env vars (`BOT_TOKEN`, `OWNER_USER_ID`, `GEMINI_API_KEY`)
4. Deploy — `WEBHOOK_URL` is taken from Render’s `RENDER_EXTERNAL_URL` automatically

### Option B — Manual Web Service

- Runtime: Python
- Build: `pip install -r requirements.txt`
- Start: `python -m chatauto`
- Health check: `/health`
- Same env vars as `.env.example`

After the first deploy, confirm logs show `Webhook set for @...`.

## 4. Test

1. From another Telegram account, message **you** (not the bot)
2. You should see a typing indicator, then a reply **as you**
3. If **you** reply manually in that chat, auto-replies pause for `OWNER_PAUSE_MINUTES` (default 30)

You can also DM the bot `/start` from your account for a status ping.

## Behavior notes

| Topic | Behavior |
|--------|----------|
| Sounds like you | Driven by `persona.txt` + Gemini |
| Memory | Last `HISTORY_LIMIT` messages per chat in SQLite |
| Profile | Name, username, bio via `getChat` when available |
| Manual override | Your own messages pause the bot for that chat |
| Free Render | Disk is ephemeral — history can reset on redeploy. Upgrade + attach a Disk at `/data` and set `DATA_DIR=/data` for persistence |
| Telegram limit | Business bots usually only reply in chats active in the last ~24h |

## Local run (with webhook tunnel)

```bash
# terminal 1
ngrok http 8080

# put the https URL into WEBHOOK_URL, then:
python -m chatauto
```

## Safety

This replies **as you** with no bot label for the other person. Start with a few contacts, tune `persona.txt`, and keep money / sensitive topics cautious in the persona rules.
