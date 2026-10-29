# Shouko

A playful, slightly sassy Genshin Impact chat bot for your Discord server.
She chats when you @her, reply to her, or say her name, remembers the last few
messages in each channel, and throws GIFs for greetings, hype moments and gacha pain.

Built with Python + [discord.py](https://discordpy.readthedocs.io/) + the
[Groq](https://console.groq.com) API (free tier).

## What she does

- Replies when someone **@mentions her**, **replies to her message**, or says "**shouko**"
- Remembers the last 10 messages per channel, so conversations feel continuous
- Sends a random GIF for: hi/hello, good morning, good night, hype/pog, sad, lol, and gacha/pulls/luck
- Knows Genshin Impact (characters, banners, resin, artifacts, 50/50 pain) and talks like a player

## Setup (one time)

1. **Discord developer portal** (https://discord.com/developers/applications):
   - Your application -> **Bot** -> copy the **token** (Reset Token if needed)
   - Same page, scroll down -> switch ON **Message Content Intent** (required!)
   - **OAuth2** -> scope `bot`, permissions `Send Messages` + `Read Message History`
     -> open the generated URL to invite her to your server
2. **Groq**: make a free API key at https://console.groq.com -> API Keys

## Run on Android with Termux (free, your spare phone)

Runs great on a spare Android phone plugged into the charger. Free forever, no card.

1. Install **Termux from F-Droid** (https://f-droid.org/en/packages/com.termux/).
   Do NOT use the Play Store version - it is outdated and broken.
2. In Termux:

```bash
pkg update && pkg install python git -y
git clone https://github.com/fazaldeveloper007/shouko-bot.git
cd shouko-bot
pip install -r requirements.txt
export DISCORD_TOKEN="your-bot-token"
export GROQ_API_KEY="your-groq-key"
python bot.py
```

   You should see `Shouko is online` in a few seconds. Keep the Termux
   window open (you can switch apps, just don't swipe Termux away).

3. **Keep her alive 24/7:**
   - Run `termux-wake-lock` so Android doesn't freeze the process when the
     screen is off (needs the free **Termux:API** companion app from F-Droid).
   - Android Settings -> Apps -> Termux -> Battery -> **Unrestricted**
     (skips battery optimization that would otherwise kill her).
   - For autostart after a reboot, install **Termux:Boot** from F-Droid and
     add `~/.termux/boot/start-shouko.sh`:

```bash
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd ~/shouko-bot
export DISCORD_TOKEN="your-bot-token"
export GROQ_API_KEY="your-groq-key"
python bot.py >> shouko.log 2>&1
```

   Tip: instead of `export` lines you can also drop a `.env` file next to
   `bot.py` (copy `.env.example` and fill in your two keys) - she reads it
   automatically.

## Run locally (optional)

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your two keys
python bot.py
```

## Deploy with Docker (any Docker host)

The included `Dockerfile` works on any Docker host or container platform
(Railway, Fly.io, a VPS, etc.): set `DISCORD_TOKEN` and `GROQ_API_KEY` as
environment variables and deploy.

## Make her yours

- **Personality**: edit `persona.py` (it's plain English, no code)
- **GIFs**: add/remove URLs in `gifs.py`, or add new keyword triggers in `bot.py` (`GIF_TRIGGERS`)
- **Model**: set `GROQ_MODEL` (default `openai/gpt-oss-120b`; `openai/gpt-oss-20b` is faster and lighter)
- **Name**: set `BOT_NAME` and she responds to the new name instead

## Files

| File | What it's for |
|---|---|
| `bot.py` | all the logic: when to reply, memory, GIF triggers |
| `persona.py` | her personality (edit this to change who she is) |
| `gifs.py` | the GIF library (every URL verified working) |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | for Docker hosts (optional; Termux doesn't need it) |
| `.env.example` | which environment variables she needs |
