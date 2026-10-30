"""
Kristy - a Genshin Impact Discord chat bot.

She chats when you @her, reply to one of her messages, or say her name,
and throws GIFs for greetings, hype moments, gacha pain and character
name mentions (she keeps gifs for every playable character).
When Gemini is the brain she can also SEE images sent to her (attachments
or pasted image links) and reacts to memes, screenshots and fanart.

Config (environment variables):
  DISCORD_TOKEN   bot token from the Discord developer portal (required)
  GEMINI_API_KEY  API key from aistudio.google.com (main brain, free tier)
  GEMINI_MODEL    (optional) Gemini model id, default gemini-3.5-flash-lite
  GROQ_API_KEY    (optional fallback) API key from console.groq.com, used when
                  GEMINI_API_KEY is not set or the Gemini call itself fails
  GROQ_MODEL      (optional) Groq model id, default openai/gpt-oss-120b
  BOT_NAME        (optional) her name, default "Kristy"
  MEMORY_DIR      (optional) where member diaries live, default ./memory
  SEARCH_PROVIDER (optional) web search provider, default "tavily"
  SEARCH_API_KEY  (optional) Tavily API key from https://tavily.com (free tier:
                  1,000 searches/month, no credit card). Without it Tavily's
                  free keyless mode is used, so search works out of the box;
                  the key just raises the limits and reliability
  SEARCH_MAX_PER_DAY  (optional) client-side cap on search calls, default 25
"""

import os
import re
import base64
import random
import asyncio
import logging
import time
from collections import defaultdict, deque

import aiohttp
import discord

from persona import SYSTEM_PROMPT
from gifs import GIFS, CHARACTER_GIFS
import diary

# Local dev convenience: load a .env file if one exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kristy")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
BOT_NAME = os.getenv("BOT_NAME", "Kristy")
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "tavily").strip().lower()
SEARCH_API_KEY = (os.environ.get("SEARCH_API_KEY") or "").strip()
# Tavily search endpoint, called directly with aiohttp like everything else
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
try:
    SEARCH_MAX_PER_DAY = max(0, int(os.getenv("SEARCH_MAX_PER_DAY", "25")))
except ValueError:
    SEARCH_MAX_PER_DAY = 25

# Groq's OpenAI-compatible endpoint. We call it directly instead of using the
# groq SDK: the SDK drags in pydantic v2 -> pydantic-core, which needs a Rust
# toolchain that does not build on Termux/Android.
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# Gemini generateContent REST endpoint, also called directly with aiohttp (no
# google SDK, so the Termux install stays pure-Python).
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

if not DISCORD_TOKEN or not (GEMINI_API_KEY or GROQ_API_KEY):
    raise SystemExit("Missing DISCORD_TOKEN or GEMINI_API_KEY (or GROQ_API_KEY). Set them as env vars (see .env.example).")

HISTORY_LEN = 20        # messages she remembers per channel
MAX_REPLY_CHARS = 1800  # Discord's limit is 2000; leave some margin

# Image vision (Gemini path only; the Groq fallback stays text-only)
MAX_IMAGE_BYTES = 8 * 1024 * 1024     # skip images bigger than ~8MB
MAX_IMAGES = 3                        # most she will look at per message
IMAGE_TIMEOUT = 25                    # seconds to download one image
IMAGE_ONLY_PROMPT = "Reply naturally to this image as Kristy."

intents = discord.Intents.default()
intents.message_content = True  # also switch this ON in the Discord developer portal!
client = discord.Client(intents=intents)

# One shared HTTP session for AI API calls
_groq_session = None

async def groq_session():
    global _groq_session
    if _groq_session is None or _groq_session.closed:
        _groq_session = aiohttp.ClientSession()
    return _groq_session

# Diary extraction cadence (free-tier friendly): at most one extra AI call per
# member every EXTRACT_EVERY addressed messages, or sooner when a message looks
# like it contains personal info, but never more often than the cooldowns.
EXTRACT_EVERY = 6          # addressed messages per member between routine extractions
EXTRACT_MEMBER_GAP = 90    # seconds: min gap between extractions for one member
EXTRACT_GLOBAL_GAP = 15    # seconds: min gap between any two extractions
SAVE_RETRY_DELAY = 45      # seconds before retrying a failed explicit save
FORGET_REPLY = "done 🥺 maine tumhari diary band karke hata di, ab mujhe tumhare baare mein kuch yaad nahi. chahoge toh phir se shuru kar sakte hain 💗"
# Sent in the channel once an explicit "yaad rakhna / remember this" is
# actually saved to the diary file - never before the fact is on disk.
SAVE_CONFIRM_REPLY = "likh liya 📔 tumhari diary mein save kar diya, ab yaad rahega 💗"

_pending = {}        # user_id -> list of (speaker, text) since last extraction
_since_extract = {}  # user_id -> addressed messages since last extraction
_last_extract = {}   # user_id -> time of last extraction
_forget_epoch = {}   # user_id -> bumped on "forget me" so in-flight extractions don't re-save
_save_pending = set()  # user_ids with an explicit save request not yet confirmed
_extract_busy = False
_last_global_extract = 0.0
_bg_tasks = set()

# Short memory per channel so conversations feel continuous
history = defaultdict(lambda: deque(maxlen=HISTORY_LEN))

# Matches her name as a whole word, case-insensitive
NAME_RE = re.compile(rf"\b{re.escape(BOT_NAME.lower())}\b")

# Questions that need live info (banners, codes, meta, news, weather...).
# Deliberately conservative: casual banter must never trigger a search call,
# because every search spends from the search model's daily quota.
SEARCH_RE = re.compile(
    r"(\b(search|google)\s+(kar|karo|karke|kar\s+do)\b|\bpata\s+kar(o|ke|na)?\b"
    r"|\b(latest|current|new|next|upcoming)\s+(news|updates?|patch|version|banners?|"
    r"characters?|regions?|events?|codes?|meta|leaks?|livestream)\b"
    r"|\bbanners?\s+(schedule|kab|kb)\b|\bmeta\s+(rn|right\s+now|abhi)\b"
    r"|\b(genshin\s+)?(news|leaks?|updates?)\s+(bata|batao|kya|suna|suno|dikha)\b"
    r"|\bkab\s+(release|launch|aayega|aayegi|ayega|ayegi|hoga|hogi|aa\s+raha|aa\s+rahi)\b"
    r"|\brelease\s+date\b|\bredeem\s+codes?\b|\bpromo\s+codes?\b"
    r"|\blivestream\b|\bspecial\s+program\b|\bmaintenance\b"
    r"|\bserver\s+(down|status)\b|\bweather\b|\bmausam\b)",
    re.IGNORECASE,
)

def looks_like_search(text):
    return bool(SEARCH_RE.search(text or ""))

# Extra instruction appended only for grounded (web-search) replies
SEARCH_HINT = (
    "\n\nThe member is asking about something current or time-sensitive, so "
    "fresh web search results are included below. Answer from them in your "
    "normal voice, with the latest accurate info. If the results do not really "
    "answer the question, say you could not find it instead of guessing."
)

_search_day = ""
_search_count = 0

def search_quota_left():
    """Client-side daily cap on search calls (resets at UTC midnight)."""
    global _search_day, _search_count
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if _search_day != today:
        _search_day, _search_count = today, 0
    return _search_count < SEARCH_MAX_PER_DAY

def mark_search_used():
    global _search_count
    _search_count += 1

# What she says if the AI brain hiccups (rate limit, network, etc.)
FALLBACK_LINES = [
    "my brain just did a 404, say that again?",
    "give me a sec, Paimon ate my thoughts...",
    "lagging harder than 300 ping in the Abyss, one more time?",
]

# What she says when the AI brain answers with nothing at all (empty completion)
EMPTY_REPLY_FALLBACK = "umm mera dimaag ek second ke liye blank ho gaya 🥺 phir se bologe?"

# What she says when someone asks for a character's gif. Canned lines, not an
# AI call: a gif request costs zero API calls, and she can never claim she
# "can't send gifs" while literally sending one.
CHARACTER_GIF_LINES = [
    "ye le 📦",
    "lo ji, specially tumhare liye 💗",
    "yahan, pakdo 🎁",
    "ek gif aa gaya, enjoy karo ✨",
    "here you go~ 💕",
    "lijo, tumhara favourite 😌",
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

# Someone asking for a specific character's gif ("send a neuvillette gif")
GIF_WORD_RE = re.compile(r"\b(gif|gifs|jif)\b", re.IGNORECASE)

# alias (lowercase) -> canonical character key, longest aliases first so
# "the tsaritsa" wins over a shorter overlapping alias
_CHAR_ALIAS_PAIRS = sorted(
    (
        (alias.lower(), name)
        for name, info in CHARACTER_GIFS.items()
        for alias in info["aliases"]
    ),
    key=lambda pair: -len(pair[0]),
)
_CHAR_ALIAS_RES = [
    (re.compile(r"\b" + re.escape(alias) + r"\b", re.IGNORECASE), name)
    for alias, name in _CHAR_ALIAS_PAIRS
]

def detect_character_gif(text):
    """Return the character key when the message asks for that character's gif, else None."""
    if not GIF_WORD_RE.search(text):
        return None
    for rx, name in _CHAR_ALIAS_RES:
        if rx.search(text):
            return name
    return None

# A message that merely MENTIONS a character (no "gif" keyword) can also earn a
# gif. One combined regex over every alias: the leftmost match wins (the
# primary character of the message); ties go to the longest alias.
_MENTION_ALIAS_MAP = {}
for _name, _info in CHARACTER_GIFS.items():
    for _alias in _info["aliases"]:
        _MENTION_ALIAS_MAP[_alias.lower()] = _name
# Aliases that double as everyday English words: fine for explicit gif
# requests, too noisy as mention triggers (the server itself is "Gaming Verse").
MENTION_SKIP_ALIASES = {"gaming"}
_MENTION_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(a)
        for a in sorted(
            (a for a in _MENTION_ALIAS_MAP if a not in MENTION_SKIP_ALIASES),
            key=lambda a: -len(a),
        )
    ) + r")\b",
    re.IGNORECASE,
)

MENTION_GIF_COOLDOWN = 90   # seconds between mention-triggered gifs per channel
_last_mention_gif = {}      # channel_id -> monotonic time of the last one

def detect_character_mention(text):
    """Return the character key for a plain name mention (no "gif" keyword), else None."""
    m = _MENTION_RE.search(text or "")
    if not m:
        return None
    return _MENTION_ALIAS_MAP.get(m.group(1).lower())

def mention_gif_allowed(channel_id):
    return time.monotonic() - _last_mention_gif.get(channel_id, -1e9) >= MENTION_GIF_COOLDOWN

def short_hype_message(text):
    """True when the message is basically just a name or short hype about the
    character ("NEUVILLETTE!!", "furina my queen"): a few words, no question."""
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return 0 < len(words) <= 4

def gif_note(desc):
    """System-prompt note so the brain KNOWS a gif went out with this reply and
    can never contradict it (the "I can't send gifs" bug)."""
    return (
        f"\n\n[Context: you are also sending a {desc} GIF in the channel along "
        "with this reply - the member already sees it. You may react to it "
        "naturally ('ye lo', 'look at her'), but never deny sending it or say "
        "you cannot send gifs.]"
    )

# --- Emoji reactions -------------------------------------------------------
# The brain may start its reply with an optional marker, [[react:<emoji>]],
# which is stripped from the visible reply and applied as a real reaction on
# the member's message. persona.py keeps it rare (genuinely shocking, dark,
# hilarious or heartwarming messages only); the per-channel cooldown below is
# a hard backstop, and on the zero-API gif paths a small random heuristic
# reacts instead. Costs no extra AI calls, and a missing "Add Reactions"
# permission only logs once - replying never breaks over a reaction.
REACTION_RE = re.compile(r"^\s*\[\[react:\s*(.+?)\s*\]\]\s*", re.DOTALL)
REACTION_COOLDOWN = 120   # seconds between her reactions per channel
REACTION_CHANCE = 0.3     # on the zero-API gif paths only; the AI path self-regulates
_last_reaction = {}       # channel_id -> monotonic time of her last reaction
_reaction_warned = False

# kind -> emojis she may react with on the zero-API gif paths
GIF_REACTIONS = {
    "character": ["\U0001f60d", "\u2728", "\u2764\ufe0f", "\U0001faf6"],
    "good_morning": ["\U0001f305", "\u2600\ufe0f", "\u2615"],
    "good_night": ["\U0001f319", "\U0001f634", "\U0001f4a4"],
    "hello": ["\U0001f44b", "\U0001f497", "\u2728"],
    "hype": ["\U0001f525", "\U0001f389", "\u26a1"],
    "sad": ["\U0001fac2", "\U0001f499", "\U0001f622"],
    "laugh": ["\U0001f602", "\U0001f480", "\U0001f923"],
    "luck": ["\U0001f340", "\u2728", "\U0001f64f"],
}

def extract_reaction(reply):
    """Split an optional leading [[react:emoji]] marker off a reply.

    Returns (emoji_or_None, clean_reply). A junk marker (ascii words, urls,
    anything long) is still stripped so it never leaks into chat.
    """
    m = REACTION_RE.match(reply or "")
    if not m:
        return None, reply
    emoji = m.group(1).strip()
    clean = reply[m.end():].lstrip()
    if not emoji or len(emoji) > 12 or re.search(r"[a-z0-9:/\[\]]", emoji, re.IGNORECASE):
        return None, clean
    return emoji, clean

def heuristic_reaction(kind):
    """Small random chance of a fitting reaction on the zero-API gif paths."""
    if random.random() >= REACTION_CHANCE:
        return None
    return random.choice(GIF_REACTIONS.get(kind) or GIF_REACTIONS["character"])

async def try_reaction(message, emoji):
    """Fail-open: missing Add Reactions permission or a rejected emoji logs
    once and never breaks replying."""
    global _reaction_warned
    if not emoji or not reaction_allowed(message.channel.id):
        return
    try:
        await message.add_reaction(emoji)
        _last_reaction[message.channel.id] = time.monotonic()
    except Exception as e:
        if not _reaction_warned:
            _reaction_warned = True
            log.warning("Could not add reaction (bot may lack the Add Reactions permission): %s", e)

def reaction_allowed(channel_id):
    return time.monotonic() - _last_reaction.get(channel_id, -1e9) >= REACTION_COOLDOWN

def message_has_image(message):
    """True when the message carries an image attachment or an embedded image."""
    for att in message.attachments:
        if (att.content_type or "").lower().startswith("image/"):
            return True
    for embed in message.embeds:
        if getattr(embed.image, "url", None) or getattr(embed.thumbnail, "url", None):
            return True
    return False

async def collect_image_parts(message):
    """Download the message's images and return Gemini inline_data parts.

    Fail-open: every problem (bad status, non-image, oversized, network) is
    logged and that image is skipped, so replying never breaks over an image.
    """
    urls = []  # (url, known mime type or None)
    seen = set()
    for att in message.attachments:
        mime = (att.content_type or "").split(";")[0].strip().lower()
        if not mime.startswith("image/"):
            continue
        if att.size and att.size > MAX_IMAGE_BYTES:
            log.warning("Skipping oversized attachment (%d bytes): %s", att.size, att.url)
            continue
        if att.url not in seen:
            seen.add(att.url)
            urls.append((att.url, mime))
    for embed in message.embeds:  # pasted image links show up as embeds
        for media in (embed.image, embed.thumbnail):
            url = getattr(media, "url", None)
            if url and url not in seen:
                seen.add(url)
                urls.append((url, None))

    parts = []
    if not urls:
        return parts
    session = await groq_session()
    timeout = aiohttp.ClientTimeout(total=IMAGE_TIMEOUT)
    for url, mime in urls[:MAX_IMAGES]:
        try:
            async with session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    log.warning("Image download failed (HTTP %s): %s", resp.status, url)
                    continue
                mime = mime or (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if not mime.startswith("image/"):
                    log.warning("Skipping non-image embed (%s): %s", mime or "unknown type", url)
                    continue
                data = await resp.read()
                if len(data) > MAX_IMAGE_BYTES:
                    log.warning("Skipping oversized image (%d bytes): %s", len(data), url)
                    continue
                parts.append({"inline_data": {"mime_type": mime,
                                              "data": base64.b64encode(data).decode("ascii")}})
        except Exception as e:
            log.warning("Image download failed for %s: %s", url, e)
    return parts

def ping_reply_text(user_id, text):
    """Add exactly one explicit ping for the member this reply is for.

    Strip mention syntax from generated/canned text first, so a reply cannot
    accidentally ping someone else or duplicate the intended member mention.
    """
    text = re.sub(r"<@!?\d+>|<@&\d+>|@everyone|@here", "", text or "", flags=re.IGNORECASE).strip()
    return f"<@{user_id}>" + (f" {text}" if text else "")


def reply_mentions(message):
    """Allow only the addressed member to be pinged, never roles/everyone."""
    return discord.AllowedMentions(
        users=[message.author], roles=False, everyone=False, replied_user=False
    )


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

def with_diary(prompt, diary_block):
    return f"{prompt}\n\n{diary_block}" if diary_block else prompt

async def gemini_generate(payload, url=GEMINI_URL):
    """One generateContent call, returning the decoded response.

    Different models accept different generationConfig knobs (the Gemini 3
    models disagree on which thinkingLevel values are valid, and a lite model
    may reject responseMimeType). If the model rejects an optional field
    (HTTP 400 naming it), drop that field and retry, so a GEMINI_MODEL switch
    never silently breaks her. Anything else raises.
    """
    session = await groq_session()
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"x-goog-api-key": GEMINI_API_KEY}
    generation = payload.get("generationConfig")
    for _attempt in range(3):
        async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
            data = await resp.json(content_type=None)
            if resp.status == 200:
                return data
            if resp.status == 400 and generation:
                err = str(data).lower().replace("_", "")
                for field in ("thinkingConfig", "responseMimeType"):
                    if field in generation and field.lower() in err:
                        log.warning("Model rejected generationConfig.%s; retrying without it", field)
                        generation.pop(field)
                        break
                else:
                    raise RuntimeError(f"Gemini API {resp.status}: {data}")
                continue
            raise RuntimeError(f"Gemini API {resp.status}: {data}")
    raise RuntimeError("Gemini API: model rejected all optional config fields")

async def call_extractor(prompt):
    """One small, low-temperature AI call that returns raw text (JSON expected)."""
    session = await groq_session()
    timeout = aiohttp.ClientTimeout(total=30)
    if GEMINI_API_KEY:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 600, "temperature": 0.2,
                                 "responseMimeType": "application/json"},
        }
        data = await gemini_generate(payload)
        parts = ((data["candidates"][0].get("content") or {}).get("parts")) or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 600,
        "temperature": 0.2,
    }
    async with session.post(GROQ_CHAT_URL, json=payload,
                            headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, timeout=timeout) as resp:
        data = await resp.json(content_type=None)
        if resp.status != 200:
            raise RuntimeError(f"Groq API {resp.status}: {data}")
        return data["choices"][0]["message"]["content"] or ""

def _requeue_exchanges(user_id, exchanges):
    """Put failed-extraction exchanges back on the pending buffer so a later
    extraction can still pick them up (they were consumed optimistically)."""
    buf = _pending.setdefault(user_id, [])
    merged = exchanges + buf
    seen = set()
    deduped = [ex for ex in merged if not (ex in seen or seen.add(ex))]
    _pending[user_id] = deduped[-12:]
    _last_extract.pop(user_id, None)  # the failure must not start the cooldown

async def extract_and_save(user_id, display_name, exchanges, epoch, channel=None):
    """Background: extract durable facts from recent exchanges and merge them.

    An explicit "yaad rakhna / remember this" request is retried once after a
    pause when the extraction fails, and she confirms in the channel once the
    fact is actually on disk. Never raises; never touches the reply path.
    """
    global _extract_busy
    confirming = user_id in _save_pending
    attempts = 2 if confirming else 1
    ok = False
    try:
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(SAVE_RETRY_DELAY)
            try:
                mem = diary.load(user_id)
                raw = await call_extractor(diary.extraction_prompt(mem, display_name, exchanges))
                name, facts, parsed = diary.parse_extraction(raw)
                if not parsed:
                    raise ValueError(f"extractor returned unusable output: {raw[:160]!r}")
                if _forget_epoch.get(user_id, 0) != epoch:
                    _save_pending.discard(user_id)
                    return  # member asked to be forgotten while we were thinking
                if facts or name:
                    mem = diary.load(user_id)  # re-read: it may have changed meanwhile
                    diary.merge(mem, facts, name=name)
                    if not diary.save(user_id, mem):
                        raise IOError("diary.save() returned False")
                    log.info("Diary updated for %s (+%d facts, %d total)", user_id, len(facts), len(mem["facts"]))
                ok = True
                break
            except Exception as e:
                log.warning("Diary extraction attempt %d/%d failed for %s: %s",
                            attempt + 1, attempts, user_id, e)
        if ok:
            if confirming:
                _save_pending.discard(user_id)
                if channel is not None:
                    try:
                        await channel.send(SAVE_CONFIRM_REPLY)
                    except Exception:
                        log.warning("Could not send save confirmation in channel %s",
                                    getattr(channel, "id", channel))
        else:
            _requeue_exchanges(user_id, exchanges)
    finally:
        _extract_busy = False

def maybe_schedule_extraction(user_id, display_name, text, channel=None):
    """Decide (cheaply, no AI call) whether to run an extraction now. Never raises.

    An explicit save request ("yaad rakhna...", "remember this", "likh lo")
    bypasses the per-member cooldown so the save happens right away; it still
    respects the global gap and the one-at-a-time guard (a skipped request
    stays owed in _save_pending and fires on the member's next message).
    """
    global _extract_busy, _last_global_extract
    try:
        _since_extract[user_id] = _since_extract.get(user_id, 0) + 1
        now = time.monotonic()
        if diary.looks_like_save_request(text):
            _save_pending.add(user_id)
        explicit = user_id in _save_pending
        due = explicit or diary.looks_like_self_info(text) or _since_extract[user_id] >= EXTRACT_EVERY
        if not due or _extract_busy:
            return
        if not explicit and now - _last_extract.get(user_id, -1e9) < EXTRACT_MEMBER_GAP:
            return
        if now - _last_global_extract < EXTRACT_GLOBAL_GAP:
            return
        exchanges = list(_pending.get(user_id, []))
        if not exchanges:
            return
        _pending[user_id] = []
        _since_extract[user_id] = 0
        _last_extract[user_id] = now
        _last_global_extract = now
        _extract_busy = True
        task = asyncio.create_task(
            extract_and_save(user_id, display_name, exchanges, _forget_epoch.get(user_id, 0), channel)
        )
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except Exception as e:
        _extract_busy = False
        log.warning("Could not schedule diary extraction: %s", e)

async def think_groq(channel_id, diary_block="", extra_system=""):
    """Fallback brain: ask Groq for Kristy's next line from the channel history."""
    messages = [{"role": "system", "content": with_diary(SYSTEM_PROMPT, diary_block) + extra_system}]
    for author, content in history[channel_id]:
        if author == BOT_NAME:
            messages.append({"role": "assistant", "content": content})
        else:
            # fold the speaker's name in so she knows who said what
            messages.append({"role": "user", "content": f"{author}: {content}"})

    session = await groq_session()
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "max_tokens": 500,
        "temperature": 0.9,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    timeout = aiohttp.ClientTimeout(total=30)
    async with session.post(GROQ_CHAT_URL, json=payload, headers=headers, timeout=timeout) as resp:
        data = await resp.json(content_type=None)
        if resp.status != 200:
            raise RuntimeError(f"Groq API {resp.status}: {data}")
        reply_text = (data["choices"][0]["message"]["content"] or "").strip()
        if not reply_text:
            log.warning("Groq returned an empty completion (HTTP %s): %s", resp.status, data)
        return reply_text

async def think_gemini(channel_id, diary_block="", image_parts=None, extra_system=""):
    """Main brain: ask Gemini (generateContent REST) for Kristy's next line."""
    contents = []
    for author, content in history[channel_id]:
        if author == BOT_NAME:
            role, text = "model", content
        else:
            # fold the speaker's name in so she knows who said what
            role, text = "user", f"{author}: {content}"
        if contents and contents[-1]["role"] == role:
            # Gemini prefers alternating turns: merge consecutive same-role messages
            contents[-1]["parts"][0]["text"] += "\n" + text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    while contents and contents[0]["role"] != "user":
        contents.pop(0)  # a conversation must start with a user turn
    if not contents:
        return ""
    if image_parts and contents[-1]["role"] == "user":
        # images ride along with the CURRENT turn only; the history deque
        # keeps text only, so old turns never carry image tokens
        contents[-1]["parts"].extend(image_parts)

    payload = {
        "system_instruction": {"parts": [{"text": with_diary(SYSTEM_PROMPT, diary_block) + extra_system}]},
        "contents": contents,
        # thinkingLevel "low" is valid on gemini-3.8-flash, 3.6-flash and the
        # 3.5 flash-lite models; "minimal" errors on 3.8-flash, and
        # gemini_generate() drops the field entirely if a model rejects it.
        "generationConfig": {"maxOutputTokens": 2048, "thinkingConfig": {"thinkingLevel": "low"}, "temperature": 0.9},
    }
    data = await gemini_generate(payload)
    try:
        candidate = data["candidates"][0]
        parts = (candidate.get("content") or {}).get("parts") or []
        reply_text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        candidate = None
        reply_text = ""
    if not reply_text:
        # blocked prompt (promptFeedback), safety finishReason, empty parts, etc.
        log.warning(
            "Gemini returned no text (finishReason=%s): %s",
            (candidate or {}).get("finishReason"), data,
        )
    return reply_text

async def fetch_search_results(query):
    """Ask the web about a query and return a compact digest for the brain.

    Tavily (free, no credit card): with SEARCH_API_KEY set the request uses
    the account's 1,000 free monthly credits; without a key it uses Tavily's
    free keyless mode, so search works out of the box. Raises on any failure -
    the caller falls back to the normal brain, so search can never break her.
    """
    if SEARCH_PROVIDER != "tavily":
        raise RuntimeError(f"unknown SEARCH_PROVIDER: {SEARCH_PROVIDER!r}")
    payload = {"query": query, "max_results": 5, "search_depth": "basic",
               "include_answer": True}
    headers = {"Content-Type": "application/json"}
    if SEARCH_API_KEY:
        headers["Authorization"] = f"Bearer {SEARCH_API_KEY}"
    else:
        headers["X-Tavily-Access-Mode"] = "keyless"
    session = await groq_session()
    timeout = aiohttp.ClientTimeout(total=20)
    async with session.post(TAVILY_SEARCH_URL, json=payload, headers=headers, timeout=timeout) as resp:
        data = await resp.json(content_type=None)
        if resp.status != 200:
            raise RuntimeError(f"Tavily API {resp.status}: {data}")
    answer = (data.get("answer") or "").strip()
    lines = []
    if answer:
        lines.append(f"Quick answer: {answer}")
    for r in (data.get("results") or [])[:5]:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip()
        snippet = (r.get("content") or "").strip()
        url = (r.get("url") or "").strip()
        if title or snippet:
            lines.append(f"- {title}: {snippet} ({url})".strip())
    digest = "\n".join(lines).strip()
    if not digest:
        raise RuntimeError("search returned no usable results")
    return digest[:3000]

def latest_question(channel_id):
    """The most recent non-Kristy message in this channel (the search query)."""
    for author, content in reversed(history[channel_id]):
        if author != BOT_NAME:
            return content
    return ""

async def think_search(channel_id, diary_block="", extra_system=""):
    """Web-informed brain for questions about current events.

    Searches the web with the configured provider (Tavily by default), then
    lets the normal chat model answer from those results in persona - so a
    search costs one provider call plus one ordinary chat-model call, and no
    special grounded model is needed. Raises on any failure; the caller falls
    back to the normal brain.
    """
    query = latest_question(channel_id)
    if not query:
        raise RuntimeError("nothing to search for")
    digest = await fetch_search_results(query)
    search_block = f"{SEARCH_HINT}\n\nWeb search results for \"{query}\":\n{digest}"
    return await think(channel_id, diary_block, extra_system=extra_system + search_block)

async def think(channel_id, diary_block="", image_parts=None, extra_system=""):
    """Gemini is the main brain; Groq is the fallback.

    Groq is used when no GEMINI_API_KEY is set, or when the Gemini call itself
    fails (429/503/network) and a GROQ_API_KEY is available. An empty Gemini
    answer is not retried on Groq (usually a safety block, which Groq would
    likely also block). image_parts (Gemini only) are ignored by the Groq
    fallback, which replies text-only.
    """
    if GEMINI_API_KEY:
        try:
            return await think_gemini(channel_id, diary_block, image_parts, extra_system)
        except Exception as e:
            if not GROQ_API_KEY:
                raise
            log.warning("Gemini brain failed, falling back to Groq: %s", e)
    return await think_groq(channel_id, diary_block, extra_system)

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

    user_id = message.author.id
    speaker = message.author.display_name

    has_image = message_has_image(message)
    if has_image and not content:
        # image-only message: give her something to react to
        content = IMAGE_ONLY_PROMPT
        history[message.channel.id].append((speaker, content))

    # "forget me": delete this member's diary file, no AI call needed
    if diary.wants_forget(content):
        _forget_epoch[user_id] = _forget_epoch.get(user_id, 0) + 1
        _pending.pop(user_id, None)
        _since_extract.pop(user_id, None)
        _save_pending.discard(user_id)
        diary.forget(user_id)
        try:
            await message.reply(
                ping_reply_text(message.author.id, FORGET_REPLY),
                mention_author=False,
                allowed_mentions=reply_mentions(message),
            )
        except Exception:
            log.exception("Failed to send forget reply in channel %s", message.channel.id)
        return

    # load this member's diary (fail-open: any problem just means no diary block)
    try:
        diary_block = diary.prompt_block(diary.load(user_id), speaker)
    except Exception as e:
        log.warning("Diary load failed for %s: %s", user_id, e)
        diary_block = ""

    character = detect_character_gif(content)
    gif_desc = None
    if character:
        # gif request: send the gif with a short canned line and skip the AI
        # brain entirely - zero API calls, and no chance of a hallucinated
        # "I can't send gifs" paragraph alongside the gif
        gif_url = random.choice(CHARACTER_GIFS[character]["gifs"])
        gif_desc = f"{character} character"
        reply = random.choice(CHARACTER_GIF_LINES)
        reaction = heuristic_reaction("character")
    else:
        mention = detect_character_mention(content)
        if mention and short_hype_message(content):
            # the message is basically just the character's name or short hype
            # about them: same zero-API short-circuit as an explicit gif request
            gif_url = random.choice(CHARACTER_GIFS[mention]["gifs"])
            gif_desc = f"{mention} character"
            reply = random.choice(CHARACTER_GIF_LINES)
            reaction = heuristic_reaction("character")
        else:
            # a plain name mention earns that character's gif (one per message,
            # with a per-channel cooldown so multi-character chatter does not
            # turn into gif spam); keyword mood gifs keep working as before
            gif_url = None
            if mention and mention_gif_allowed(message.channel.id):
                gif_url = random.choice(CHARACTER_GIFS[mention]["gifs"])
                gif_desc = f"{mention} character"
                _last_mention_gif[message.channel.id] = time.monotonic()
            else:
                category = detect_gif_category(content)
                if category and GIFS.get(category):
                    gif_url = random.choice(GIFS[category])
                    gif_desc = category.replace("_", " ")

            # when a gif rides along, the brain is TOLD about it, so she can
            # never contradict the gif she just sent
            extra = gif_note(gif_desc) if gif_url else ""

            async with message.channel.typing():
                image_parts = []
                if GEMINI_API_KEY and has_image:  # Groq fallback: silently text-only
                    try:
                        image_parts = await collect_image_parts(message)
                    except Exception as e:
                        log.warning("Image collection failed: %s", e)
                        image_parts = []
                try:
                    if not has_image and looks_like_search(content) and search_quota_left():
                        # current-info question: answer from a live web search; any
                        # problem (error, quota, empty) just falls back to the
                        # normal brain, so search can never break replying
                        try:
                            mark_search_used()
                            reply = await think_search(message.channel.id, diary_block, extra)
                            if not reply:
                                raise RuntimeError("search returned no text")
                        except Exception as e:
                            log.warning("Web search failed, replying without it: %s", e)
                            reply = await think(message.channel.id, diary_block, image_parts, extra)
                    else:
                        reply = await think(message.channel.id, diary_block, image_parts, extra)
                except Exception as e:
                    log.warning("AI brain error: %s", e)
                    reply = random.choice(FALLBACK_LINES)
                reaction, reply = extract_reaction(reply)
                if not reply:
                    reply = EMPTY_REPLY_FALLBACK

    reply = reply[:MAX_REPLY_CHARS]
    stored_reply = reply + (f" [sent a {gif_desc} gif]" if gif_desc else "")
    history[message.channel.id].append((BOT_NAME, stored_reply))
    try:
        await message.reply(
            ping_reply_text(message.author.id, reply),
            mention_author=False,
            allowed_mentions=reply_mentions(message),
        )
        if gif_url:
            await message.channel.send(gif_url)  # Discord auto-embeds direct GIF links
        await try_reaction(message, reaction)
    except Exception:
        log.exception("Failed to send reply in channel %s", message.channel.id)

    # learn from this exchange in the background, after the reply is already out
    if content and reply not in FALLBACK_LINES and reply != EMPTY_REPLY_FALLBACK:
        buf = _pending.setdefault(user_id, [])
        buf.append((speaker, content[:300]))
        buf.append((BOT_NAME, reply[:300]))
        del buf[:-12]  # keep at most the last 6 exchanges
        maybe_schedule_extraction(user_id, speaker, content, message.channel)

client.run(DISCORD_TOKEN)


