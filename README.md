# Kristy

A sweet, affectionate Genshin Impact chat bot for your Discord server.
She chats when you @her, reply to her, or say her name, remembers the last few
messages in each channel, and throws GIFs for greetings, hype moments and gacha pain.

Built with Python + [discord.py](https://discordpy.readthedocs.io/) + the
[Gemini](https://aistudio.google.com) API (free tier, called directly over HTTPS - no SDK needed).
[Groq](https://console.groq.com) still works as an optional fallback brain.

## What she does

- Replies when someone **@mentions her**, **replies to her message**, or says "**kristy**"
- Remembers the last 20 messages per channel, so conversations feel continuous
- Sends a random GIF for: hi/hello, good morning, good night, hype/pog, sad, lol, and gacha/pulls/luck
- Knows Genshin Impact (characters, banners, resin, artifacts, 50/50 pain) and talks like a player

## Setup (one time)

1. **Discord developer portal** (https://discord.com/developers/applications):
   - Your application -> **Bot** -> copy the **token** (Reset Token if needed)
   - Same page, scroll down -> switch ON **Message Content Intent** (required!)
   - **OAuth2** -> scope `bot`, permissions `Send Messages` + `Read Message History`
     -> open the generated URL to invite her to your server
2. **Gemini** (main brain): make a free API key at https://aistudio.google.com -> Get API key.
   (Optional fallback: a Groq key from https://console.groq.com - used if `GEMINI_API_KEY`
   is not set, or if the Gemini call itself fails, so a rate limit never leaves her speechless.)

## Run on Android with Termux (free, your spare phone)

Runs great on a spare Android phone plugged into the charger. Free forever, no card.

1. Install **Termux from F-Droid** (https://f-droid.org/en/packages/com.termux/).
   Do NOT use the Play Store version - it is outdated and broken.
2. In Termux, run:

```bash
pkg update && pkg install python git clang nano -y
git clone https://github.com/fazaldeveloper007/shouko-bot.git
cd shouko-bot
export AIOHTTP_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 FROZENLIST_NO_EXTENSIONS=1 PROPCACHE_NO_EXTENSIONS=1
pip install -r requirements.txt
export DISCORD_TOKEN="your-bot-token"
export GEMINI_API_KEY="your-gemini-key"
termux-wake-lock
python bot.py
```

You should see `Kristy is online` in a few seconds. Keep the Termux
window open (you can switch apps, just don't swipe Termux away).

Notes:
- PyPI has no Android wheels for these packages, so the `NO_EXTENSIONS` exports
  above are required for the pip install.
- **clang** is needed to build `audioop-lts` on Python 3.14.
- `termux-wake-lock` is built into Termux; it does **not** need Termux:API.
- Android Settings -> Apps -> Termux -> Battery -> **Unrestricted** helps keep
  Android from killing the bot in the background.
- For autostart after a reboot, install **Termux:Boot** from F-Droid, open it
  once, then create `~/.termux/boot/start-shouko.sh` with:

```bash
#!/data/data/com.termux/files/usr/bin/sh
export AIOHTTP_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 FROZENLIST_NO_EXTENSIONS=1 PROPCACHE_NO_EXTENSIONS=1
export DISCORD_TOKEN="your-bot-token"
export GEMINI_API_KEY="your-gemini-key"
cd ~/shouko-bot && python bot.py >> ~/shouko.log 2>&1
```

Then make the script executable:

```bash
chmod +x ~/.termux/boot/start-shouko.sh
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
(Railway, Fly.io, a VPS, etc.): set `DISCORD_TOKEN` and `GEMINI_API_KEY` as
environment variables and deploy.

## Memory

Kristy keeps a small private "diary" for every member: their preferred name, Genshin mains,
likes, running jokes and things they asked her to remember. She uses it naturally in replies.

- Each member's diary is one file, `memory/<discord_user_id>.json`, created automatically.
  The `memory/` folder is gitignored, so `git pull` never conflicts and diaries are never committed.
- She learns in the background after replying (never delays a reply). To save the free-tier quota
  she runs one extra small AI call per member roughly every 6 messages to her, or sooner when a
  message sounds personal ("my main is...", "remember..."), with cooldowns between calls.
- Tell her to remember something directly ("yaad rakhna...", "remember this", "likh lo") and she
  saves it right away, skipping the cooldown: the fact is extracted and written to the diary file,
  retried once if the first attempt fails (e.g. rate limit), and she confirms in the channel
  ("likh liya 📔") only once it is actually saved. If every attempt fails she stays silent instead
  of lying, and retries the save on your next message.
- Each diary is capped at 40 short facts; the oldest are dropped when it is full.
- Say "forget me" (or "delete my diary") to her and that diary file is deleted.
- If a diary is missing or corrupt, she just chats normally. Set `MEMORY_DIR` to store diaries elsewhere.

## Web search (live answers)

When a message clearly asks about something current - next/upcoming banner, redeem codes,
maintenance, release dates, "search kar...", "pata kar...", weather, etc. - she answers from a
live Google Search instead of guessing. Casual chatting never triggers a search.

- Searches run on a separate model, `GEMINI_SEARCH_MODEL` (default `gemini-2.5-flash`). Google
  Search grounding is free-tier on gemini-2.5-flash (500 grounded requests/day) but NOT on the
  Gemini 3 models, so searches never eat your chat model's daily quota.
- A client-side cap (`SEARCH_MAX_PER_DAY`, default 25) limits search calls per day, resetting at
  UTC midnight. Past the cap she just answers normally.
- Fully fail-open: if the search call errors, is over quota, or returns nothing, she replies
  with her normal brain as if search never happened.

## Make her yours

- **Personality**: edit `persona.py` (it's plain English, no code)
- **GIFs**: add/remove URLs in `gifs.py`, or add new keyword triggers in `bot.py` (`GIF_TRIGGERS`)
- **Model**: set `GEMINI_MODEL` (default `gemini-3.8-flash`; the request config adapts if a model
  rejects an option, so switching models can't silently break her). Only on the Groq fallback:
  `GROQ_MODEL` (default `openai/gpt-oss-120b`). Web search uses `GEMINI_SEARCH_MODEL` (see above)
- **Name**: set `BOT_NAME` and she responds to the new name instead

## Files

| File | What it's for |
|---|---|
| `bot.py` | all the logic: when to reply, memory, GIF triggers |
| `persona.py` | her personality (edit this to change it) |
| `diary.py` | per-member memory (diary files, merge, forget) |
| `gifs.py` | the GIF library (every URL verified working) |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | for Docker hosts (optional; Termux doesn't need it) |
| `.env.example` | which environment variables she needs |
