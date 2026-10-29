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

## Deploy on Northflank (free, always on, no card)

1. Push this repo to GitHub.
2. Sign up at https://northflank.com (email or GitHub login, no card needed).
3. Create a **project**, then **Add Service** -> **Deployment** -> pick this Git repo.
4. Select the smallest compute plan (nf-compute-20, 0.2 vCPU / 512 MB is plenty;
   the free Sandbox covers up to $20/mo of resources and this uses about $5.50/mo).
5. Add two **environment variables**:
   - `DISCORD_TOKEN` = your bot token
   - `GROQ_API_KEY` = your Groq key
6. Deploy. Northflank builds from the included `Dockerfile`, runs her 24/7
   (no sleeping on the Sandbox plan) and restarts her if she crashes.

## Run locally (optional)

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your two keys
python bot.py
```

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
| `Dockerfile` | how Northflank builds and runs her |
| `.env.example` | which environment variables she needs |
