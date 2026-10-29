"""
Shouko - a playful Genshin Impact Discord chat bot.

She chats when you @her, reply to one of her messages, or say her name,
and throws GIFs for greetings, hype moments and gacha pain.

Config (environment variables):
    DISCORD_TOKEN   bot token from the Discord developer portal (required)
    GROQ_API_KEY    API key from console.groq.com (required)
    GROQ_MODEL      (optional) Groq model id, default openai/gpt-oss-120b
    BOT_NAME        (optional) her name, default "Shouko"
"""

import os
import re
import random
import logging
from collections import defaultdict, deque

import discord
from groq import AsyncGroq

from persona import SYSTEM_PROMPT
from gifs import GIFS

# Local dev convenience: load a .env file if one exists (not needed on Northflank)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("shouko")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
BOT_NAME = os.getenv("BOT_NAME", "Shouko")

if not DISCORD_TOKEN or not GROQ_API_KEY:
    raise SystemExit("Missing DISCORD_TOKEN or GROQ_API_KEY. Set them as env vars (see .env.example).")

HISTORY_LEN = 10        # messages she remembers per channel
MAX_REPLY_CHARS = 1800  # Discord's limit is 2000; leave some margin

intents = discord.Intents.default()
intents.message_content = True  # also switch this ON in the Discord developer portal!
client = discord.Client(intents=intents)
groq_client = AsyncGroq(api_key=GROQ_API_KEY)

# Short memory per channel so conversations feel continuous
history = defaultdict(lambda: deque(maxlen=HISTORY_LEN))

# Matches her name as a whole word, case-insensitive
NAME_RE = re.compile(rf"\b{re.escape(BOT_NAME.lower())}\b")

# What she says if the AI brain hiccups (rate limit, network, etc.)
FALLBACK_LINES = [
    "my brain just did a 404, say that again?",
    "give me a sec, Paimon ate my thoughts...",
    "lagging harder than 300 ping in the Abyss, one more time?",
]

# Keyword -> GIF category. First match wins, so order matters.
GIF_TRIGGERS = [
    (r"\b(good\s*morning|gm|ohayo|mornings)\b", "good_morning"),
    (r"\b(good\s*night|gn|oyasumi|sleep(y|ing)?|bed\s*time)\b", "good_night"),
    (r"\b(hi+|hello+|hey+|helo|hiya|yo|sup)\b", "hello"),
    (r"\b(pog|poggers|hype(d)?|let'?s\s*go+|lesgo|yooo+|sheesh)\b", "hype"),
    (r"\b(sad|crying|cry|depressed|rip\b|f\b)", "sad"),
    (r"\b(lol|lmao|lmfao|rofl|haha+|xd)\b", "laugh"),
    (r"\b(pulls?|wish(es)?|gacha|50/?50|pity|luck(y)?|primogems?|primos|banner|welkin)\b", "luck"),
]
GIF_RES = [(re.compile(pat, re.IGNORECASE), cat) for pat, cat in GIF_TRIGGERS]


def detect_gif_category(text):
    """Return the GIF category matching a message, or None."""
    for rx, category in GIF_RES:
        if rx.search(text):
            return category
    return None


def should_respond(message):
    """Reply when @mentioned, replied to, or name-dropped."""
    if client.user in message.mentions:
        return True
    if NAME_RE.search(message.content.lower()):
        return True
    ref = message.reference
    if ref and isinstance(ref.resolved, discord.Message) and ref.resolved.author == client.user:
        return True
    return False


async def think(channel_id):
    """Ask Groq for Shouko's next line using the channel's recent history."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for author, content in history[channel_id]:
        if author == BOT_NAME:
            messages.append({"role": "assistant", "content": content})
        else:
            # fold the speaker's name in so she knows who said what
            messages.append({"role": "user", "content": f"{author}: {content}"})
    resp = await groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=500,
        temperature=0.9,
    )
    return resp.choices[0].message.content.strip()


@client.event
async def on_ready():
    log.info("%s is online as %s", BOT_NAME, client.user)
    await client.change_presence(activity=discord.Game(name=f"Genshin Impact | @{BOT_NAME} to chat"))


@client.event
async def on_message(message):
    # ignore other bots and DMs; she's a server bot
    if message.author.bot or not message.guild:
        return

    content = message.content.strip()
    if content:
        history[message.channel.id].append((message.author.display_name, content[:300]))

    if not should_respond(message):
        return

    category = detect_gif_category(content)
    gif_url = random.choice(GIFS[category]) if category and GIFS.get(category) else None

    async with message.channel.typing():
        try:
            reply = await think(message.channel.id)
        except Exception as e:
            log.warning("Groq error: %s", e)
            reply = random.choice(FALLBACK_LINES)

    reply = reply[:MAX_REPLY_CHARS]
    history[message.channel.id].append((BOT_NAME, reply))
    await message.reply(reply, mention_author=False)
    if gif_url:
        await message.channel.send(gif_url)  # Discord auto-embeds direct GIF links


client.run(DISCORD_TOKEN)
