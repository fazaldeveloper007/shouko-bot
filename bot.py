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
  OWNER_ID        (optional) Discord user ID allowed to use owner commands
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
import aliases
import retrying
import announcements

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
OWNER_ID = (os.getenv("OWNER_ID") or "").strip()
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "tavily").strip().lower()
SEARCH_API_KEY = (os.environ.get("SEARCH_API_KEY") or "").strip()
# Tavily search endpoint, called directly with aiohttp like everything else
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
# How much retrieved context the brain gets: generous enough that detail
# survives, small enough to stay within the model's fast-reply budget.
SEARCH_DIGEST_CHARS = 6000       # cap on the whole digest handed to the brain
MAX_RESULT_SNIPPET_CHARS = 1200  # per-result cap, so one page can't eat it all
MIN_RESULT_CONTENT_CHARS = 150   # below this a result is boilerplate, not evidence
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
HISTORY_REPLY_CHARS = 1200  # cap on how much of HER OWN long replies history keeps
MAX_REPLY_CHARS = 1900  # chunk size for long replies; Discord's own limit is 2000

PING_GUIDANCE = """
OPTIONAL DISCORD PING:
Only when notifying the person would materially help, add [[ping]] at the very
end of your reply. Use it sparingly: for example, when calling this person into
a group discussion, distinguishing who you are addressing in a busy channel,
or when they explicitly asked to be pinged. Do not add it just because you are
replying to them, answering an ordinary question, or mentioning their name.
When in doubt, omit it. Never write Discord mention syntax yourself.

TAGGING ANOTHER MEMBER:
Only when the user explicitly asks you to tag/mention ANOTHER server member,
put [[tag:their name]] in your reply exactly where the mention belongs, using
the name the user gave. One tag per person. Never tag anyone you were not
asked to tag, never use it for @everyone or roles. If the request does not
name a specific person, ask who they mean instead of guessing.

NICKNAMES & VC SUMMONS (handled by code, no marker needed):
Members can teach you nicknames ("Mayank ka username mayank_123" or
"call me X") and can ask you to call named people to VC in a specific
#channel they mention. Both are handled for you, so never claim you cannot
do these; if a name or channel is unclear, the code asks instead of
guessing. You never tag anyone you were not asked to tag.
"""
PING_MARKER_RE = re.compile(r"\[\[ping\]\]", re.IGNORECASE)
TAG_MARKER_RE = re.compile(r"\[\[tag:([^\]]{1,50})\]\]", re.IGNORECASE)

# Image vision (Gemini path only; the Groq fallback stays text-only)
MAX_IMAGE_BYTES = 8 * 1024 * 1024     # skip images bigger than ~8MB
MAX_IMAGES = 3                        # most she will look at per message
IMAGE_TIMEOUT = 25                    # seconds to download one image
IMAGE_ONLY_PROMPT = "Reply naturally to this image as Kristy."
EMPTY_MESSAGE_PROMPT = "[sent a message with no text]"

def describe_stickers(message):
    """Text stand-in for Discord stickers: they arrive on message.stickers,
    not as attachments, so a sticker-only message has empty content and was
    invisible to the brain (it also left the chat history ending on Kristy's
    own turn, which Gemini rejects with a 400). The name always tells her
    what the sticker conveys; static PNG stickers are also sent as images.
    """
    notes = []
    for st in getattr(message, "stickers", None) or []:
        name = (getattr(st, "name", "") or "").strip() or "sticker"
        fmt = getattr(getattr(st, "format", None), "name", "")
        if fmt == "png":
            notes.append(f"[sent a sticker: {name}]")
        else:
            # APNG/GIF/Lottie stickers can't be fed to Gemini as images;
            # the name alone still gives her something real to react to.
            notes.append(f"[sent an animated sticker: {name}]")
    return " ".join(notes)

intents = discord.Intents.default()
intents.message_content = True  # also switch this ON in the Discord developer portal!
intents.members = True          # for new-member welcomes: enable "Server Members Intent" in the portal too
class KristyClient(discord.Client):
    async def close(self):
        tasks = list(globals().get("_bg_tasks", ()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if getattr(self, 'music', None):
            await self.music.close()
        await super().close()

client = KristyClient(intents=intents)
# Disabled by default until separately approved deployment/setup.
client.music = None
if os.getenv('MUSIC_ENABLED') == '1':
    from music.integration import Music
    client.music = Music(client)

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

# Search routing is code-driven: explicit requests always win; likely-current
# Genshin topics (especially future version/leak questions) also prefer live
# sources. Ordinary chat stays on the normal brain to conserve search quota.
SEARCH_RE = re.compile(
    r"(\b(search(?:ing)?|google)\b|\blook\s+(?:it|that|this)\s+up\b"
    r"|\b(?:try|please|can\s+you|could\s+you)\s+(?:search|google|look\s+up)\b"
    r"|\bpata\s+kar(o|ke|na)?\b|\bsearch\s+(?:kar|karo|karke|kar\s+do)\b"
    r"|\b(latest|current|new|next|upcoming)\s+(news|updates?|patch|version|banners?|"
    r"characters?|regions?|events?|codes?|meta|leaks?|livestream)\b"
    r"|\b(latest|current|new|next|upcoming)\s+(genshin\s+)?(version|patch|banner|update|leak|news)\b"
    r"|\b(?:upcoming|future|next|coming|leaks?)\b.{0,50}\b(?:genshin|version|v?\d+\.\d+|patch|banner|update)\b"
    r"|\b(?:upcoming|future|next|coming)\b.{0,40}\b(?:v?\d+\.\d+(?:\.\d+)?|version|patch)\b"
    r"|\b(?:leaks?|rumors?)\b.{0,30}\b(?:for|about|of|on)?\s*v?\d+\.\d+(?:\.\d+)?\b"
    r"|\bv?\d+\.\d+(?:\.\d+)?\s*(?:leaks?|rumors?|updates?|patch|banners?|release|news)\b"
    r"|\b(?:genshin|gi|hsr|honkai|star\s*rail|wuwa|wuthering|zzz|zenless)\b.{0,25}\bv?\d+\.\d+(?:\.\d+)?\b"
    r"|\b(?:upcoming|future|next)\s+in\s+v?\d+\.\d+(?:\.\d+)?\b"
    r"|\b(?:genshin\s+)?(?:version|patch)\s*v?\d+\.\d+(?:\.\d+)?\b"
    r"|\b(?:in|for)\s+v?\d+\.\d+(?:\.\d+)?\b"
    r"|\bbanners?\s+(schedule|kab|kb)\b|\bmeta\s+(rn|right\s+now|abhi)\b"
    r"|\b(genshin\s+)?(news|leaks?|updates?)\s+(bata|batao|kya|suna|suno|dikha)\b"
    r"|\bkab\s+(release|launch|aayega|aayegi|ayega|ayegi|hoga|hogi|aa\s+raha|aa\s+rahi)\b"
    r"|\brelease\s+date\b|\bredeem\s+codes?\b|\bpromo\s+codes?\b"
    r"|\blivestream\b|\bspecial\s+program\b|\bmaintenance\b"
    r"|\bserver\s+(down|status)\b|\bweather\b|\bmausam\b)",
    re.IGNORECASE,
)

# Guide-shaped words that become searchable when the message clearly anchors
# to the game (a game keyword or a known character name). Without the anchor,
# words like "team" or "stats" are too ordinary to spend a search on.
_SEARCH_GUIDE_WORDS_RE = re.compile(
    r"\b(build|builds|guide|guides|team|teams|comp|comps|weapons?|artifacts?|"
    r"materials?|farm(?:ing)?|rerun|re-run|stats?|kit|"
    r"aayega|aayegi|ayega|ayegi|aane\s+wala|aane\s+wali)\b",
    re.IGNORECASE,
)

def looks_like_search(text):
    t = text or ""
    if SEARCH_RE.search(t):
        return True
    if _SEARCH_GUIDE_WORDS_RE.search(t) and (
        _GENSHIN_TOPIC_RE.search(t) or detect_character_mention(t)
    ):
        return True
    return False

# --- Search query construction -------------------------------------------
# Members write things like "Ek baat batao 7.2 mei kya kya aa rha hai? Kristy
# search karo". Sending that whole sentence to the search API returns junk
# (NCERT solutions, random reels). So the query is rebuilt from topical
# tokens only: function words, politeness and search-command phrasing are
# dropped, version numbers are preserved, and a "Genshin Impact" anchor is
# prepended when the topic is clearly the game but no game is named.
_QUERY_STOPWORDS = frozenset(
    # Hinglish function words / phrasing (they poison web queries)
    "kya hai hain ho hoga hogi honge hun hu mein mei me mai ka ki ke ko se pe "
    "par tak aur ya toh to bhi hi nahi na mat ek baat abhi ab kab kaha kahan "
    "kyu kyun kaun kitna kitni kitne bata batao bataiye batao bol bolo suna "
    "suno sunao dikha dikhao dikha do dekh dekho dekha jaa jao ja rha rahi "
    "raha rahe rhe aa aayi aaya aayega aayegi aane wala wali wale gaya gai "
    "tha thi the karo kar karke kardo karna karna kare krega kregi dena do "
    "diya chahiye chahta chahti chahata mujhe meri mera hum humein apna apni "
    "tum tumhe tumhari tumhara please plz pliss yar yaar bhai bro "
    # English function words / politeness
    "a an the is are was were be been being am what whats which who whom when "
    "where why how can could should would will shall may might do does did "
    "done please plz try tell me show give find check about of on in at for "
    "to from with by and or not no yes you your ya u it its this that these "
    "those there here out up just really very much some any all ek "
    # search-command vocabulary itself (never part of the query)
    "search searching searched google googling browse browsing internet net "
    "web online pata dhundh dhundo dhundho lookup look lookup".split()
)

_GENSHIN_TOPIC_RE = re.compile(
    r"(genshin|hoyoverse|mihoyo|banner|leaks?|primogems?|redeem|patch|"
    r"livestream|special\s+program|natlan|snezhnaya|nod.?krai|fontaine|"
    r"liyue|inazuma|sumeru|mondstadt|tsaritsa|fatui|harbinger|abyss|spiral|"
    r"gacha|pity|wish|wishes|artifact|build|constellation|c\d\b|weapon|"
    r"banner|archon|resin|teapot|serenitea)",
    re.IGNORECASE,
)
_VERSION_TOKEN_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")
_WORD_TOKEN_RE = re.compile(r"[a-z][a-z0-9'._-]*")

def build_search_query(text):
    """Turn a raw member message into a focused web-search query."""
    t = NAME_RE.sub(" ", (text or "").lower())
    versions = [v.lstrip("v") for v in _VERSION_TOKEN_RE.findall(t)]
    words = _WORD_TOKEN_RE.findall(t)
    kept = []
    for w in words:
        w = w.strip(".'-_")
        if len(w) < 2 or w in _QUERY_STOPWORDS:
            continue
        if w in versions:  # already captured as a version token
            continue
        kept.append(w)
    query_words = kept + [v for v in versions if v not in kept]
    topic_is_game = bool(versions) or bool(_GENSHIN_TOPIC_RE.search(t))
    if not topic_is_game:
        # a named character also anchors the topic to the game
        lowered = t
        topic_is_game = any(
            re.search(rf"\b{re.escape(name)}\b", lowered) for name in CHARACTER_GIFS
        )
    if topic_is_game and not any(
        g in query_words for g in ("genshin", "impact", "hsr", "honkai", "wuwa", "wuthering", "zenless", "zzz")
    ):
        query_words.insert(0, "impact")
        query_words.insert(0, "genshin")
    return " ".join(query_words).strip()

def search_query_for(channel_id, requester_id=None, also_author_id=None):
    """Newest usable search topic in this channel -> (query, question).

    Search-command-only messages ("search karo", "browse karo") carry no
    topic, so walk back the channel history for the actual question - but
    only the requester's OWN messages. Another member's message is fair
    game only when the requester explicitly pointed at it (Discord reply),
    via also_author_id. Otherwise she asks what to search instead of
    guessing at a neighbour's question in a busy channel.
    """
    for author, author_id, content in reversed(history[channel_id]):
        if author == BOT_NAME:
            continue
        q = build_search_query(content)
        if len(q) < 3:
            continue
        if author_id == requester_id:
            return q, content
        if also_author_id is not None and author_id == also_author_id:
            return q, content
    return "", ""

def query_has_anchor(query):
    """True when the query itself names the game, a game term, or a known
    character - so we never send a lone unknown word (a name we don't know,
    a typo) to the search API and hand back off-topic junk."""
    q = (query or "").lower()
    if not q:
        return False
    if _GENSHIN_TOPIC_RE.search(q):
        return True
    if re.search(r"\b(genshin|impact|hsr|honkai|wuwa|wuthering|zenless|zzz)\b", q):
        return True
    return any(
        re.search(rf"\b{re.escape(name.lower())}\b", q) for name in CHARACTER_GIFS
    )

def current_context():
    """Ground her in real time so she stops quoting stale training data."""
    today = time.strftime("%A, %d %B %Y")
    return (
        f"\n\nCURRENT DATE: {today}. Your training data is old and the "
        "game has moved on since. Never state the current version, banners, "
        "leaks or news from memory. If no web search results were provided "
        "for a current-events question, say you are not sure and offer to "
        "search, instead of guessing."
    )

# Always tell both supported brains that live search exists and how the
# runtime activates it. This prevents the model from denying the capability.
SEARCH_CAPABILITY = (
    "\n\nLIVE WEB SEARCH: You can use live web search when the member explicitly "
    "asks you to search, Google, or look something up, and the bot runtime will "
    "provide successful search results below. It also searches likely-current "
    "questions such as upcoming Genshin versions, patches, banners, updates, "
    "news, and leaks. Do not say you cannot search. Only say you searched when "
    "actual results are provided; if a note says the search failed or the daily "
    "limit was reached, be transparent and do not guess current/future facts."
)

# Extra instruction appended only for grounded (web-search) replies
SEARCH_HINT = (
    "\n\nThe member asked for a web search. Fresh results are below, but the "
    "search query was auto-built from their message, so judge the results "
    "against their ACTUAL question. Answer ONLY from what the results say, in "
    "your normal voice, and naturally mention where the info came from (site "
    "or source name). If the results are off-topic or do not really answer "
    "the question (random unrelated pages, wrong topic), honestly say the "
    "search did not find the right info. NEVER fill the gap from your own "
    "memory about current or future game versions, banners, leaks or news - "
    "your training data is outdated and will be wrong. Give the member the "
    "full useful detail these results contain - the actual names, dates, "
    "numbers and specifics. A complete, longer answer is right here; do not "
    "squeeze a search answer into a one-liner."
)

# --- Model-driven search ---------------------------------------------------
# The regex fast-path above only catches explicit "search karo" commands and
# version/leak-shaped questions. It can never cover every searchable topic
# (character builds, team comps, weapons, materials, boss guides, random
# real-world facts). So the BRAIN itself can ask for one live search per
# reply by emitting a [[search: query]] marker; the runtime runs it and makes
# a second brain call with the results. The marker text is never shown.
MODEL_SEARCH_RE = re.compile(r"\[\[\s*search\s*:\s*([^\]\n]{3,200}?)\s*\]\]", re.IGNORECASE)

MODEL_SEARCH_CAPABILITY = (
    "\n\nSEARCH ON YOUR OWN: When a member asks about something you are not "
    "sure of - character builds, team comps, weapons, artifacts, materials, "
    "boss or domain guides, current or upcoming versions, banners, leaks, "
    "news, or any real-world fact - do NOT guess from memory and do NOT just "
    "say you don't know. Instead reply with ONLY a search request on its own "
    "line, exactly like this: [[search: short clean english query]]. The bot "
    "runtime will run that search and hand you the results, and then you "
    "answer from them. Use it at most once per reply, and only when you "
    "actually need outside info - never for chit-chat, greetings, opinions, "
    "or static game lore you already know well. NEVER claim you searched the "
    "internet or describe what results said unless the runtime actually gave "
    "you results - the [[search: ...]] marker is the ONLY way to search, and "
    "members never see the marker."
)

MODEL_SEARCH_HINT = (
    "\n\nYou asked for a web search and the runtime just ran it - fresh "
    "results are below. Answer ONLY from what the results say, in your normal "
    "voice, and naturally mention where the info came from (site or source "
    "name). If the results are off-topic, empty, or do not really answer the "
    "question, say honestly that the search did not find it - and when there "
    "is a likely reason (for example the character or thing is not released "
    "or playable yet, or only unconfirmed leaks exist), explain THAT instead "
    "of a flat 'nahi mila'. NEVER fill the gap from your own memory about "
    "current or future game versions, banners, leaks, builds or news - your "
    "training data is outdated and will be wrong."
)

MODEL_SEARCH_FAIL_NOTE = (
    "\n\nYou asked for a web search, but the search FAILED (or the daily "
    "search limit was reached), so NO results were provided. Tell the member "
    "plainly, in your normal voice, that the search did not work right now "
    "and they can try again in a bit. Do not guess the answer from memory, "
    "and do not say you searched the internet - you did not get any results."
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
REACTION_COOLDOWN = 60    # seconds between her reactions per channel
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
    for st in getattr(message, "stickers", None) or []:
        if getattr(getattr(st, "format", None), "name", "") == "png":
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
    for st in getattr(message, "stickers", None) or []:  # stickers aren't attachments
        # Only static PNG stickers: Gemini accepts image/png but not APNG,
        # GIF or Lottie, and an unsupported type gets the whole reply a 400.
        if getattr(getattr(st, "format", None), "name", "") != "png":
            continue
        url = getattr(st, "url", None)
        if url and url not in seen:
            seen.add(url)
            urls.append((url, "image/png"))

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

def extract_ping_request(text):
    """Remove the model-only opt-in marker and return whether it was present."""
    text = text or ""
    requested = bool(PING_MARKER_RE.search(text))
    return requested, PING_MARKER_RE.sub("", text).strip()


def sanitize_mentions(text):
    """Strip all mention syntax the model may have written."""
    return re.sub(r"<@!?\d+>|<@&\d+>|@everyone|@here", "", text or "", flags=re.IGNORECASE).strip()


def ping_reply_text(user_id, text, ping=False):
    """Sanitize mention syntax; optionally ping only the addressed member."""
    text = sanitize_mentions(text)
    if ping:
        return f"<@{user_id}>" + (f" {text}" if text else "")
    return text


def chunk_reply(text, limit=MAX_REPLY_CHARS):
    """Split a long reply into Discord-sized messages at paragraph, then
    sentence, then word boundaries. Short replies pass through untouched."""
    text = text or ""
    if len(text) <= limit:
        return [text]
    chunks = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = max(rest.rfind(". ", 0, limit), rest.rfind("! ", 0, limit),
                      rest.rfind("? ", 0, limit))
            if cut >= limit // 2:
                cut += 1  # keep the punctuation with its sentence
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def reply_mentions(message, extra_users=()):
    """Allow only the addressed member (plus explicitly tagged members) to be
    pinged, never roles/everyone."""
    seen, users = set(), []
    for u in (message.author, *extra_users):
        if u.id not in seen:
            seen.add(u.id)
            users.append(u)
    return discord.AllowedMentions(
        users=users, roles=False, everyone=False, replied_user=False
    )


def find_tag_target(guild, name):
    """Resolve a member by the name the user gave. Returns (member, unclear):
    member only on one confident match; unclear when ambiguous or not found."""
    if guild is None:
        return None, True
    n = (name or "").strip().lstrip("@").casefold()
    if not n:
        return None, True
    # Taught server nicknames win over fuzzy matching: they are explicit
    # mappings stored by member ID, verified against the live member list
    # here. A stale alias (member left) is unresolvable - never fall through
    # to a fuzzy guess that could tag the wrong person.
    try:
        aliased_id = aliases.resolve(guild.id, name)
    except Exception:
        aliased_id = None
    if aliased_id is not None:
        member = guild.get_member(aliased_id)
        if member is not None:
            return member, False
        return None, True
    members = list(guild.members)
    exact = [m for m in members if n in {
        m.display_name.casefold(), m.name.casefold(), (m.global_name or "").casefold()
    }]
    if len(exact) == 1:
        return exact[0], False
    if len(exact) > 1:
        return None, True
    partial = [m for m in members
               if m.display_name.casefold().startswith(n) or m.name.casefold().startswith(n)]
    if len(partial) == 1:
        return partial[0], False
    return None, True


def resolve_tag_requests(message, text):
    """Swap the model's [[tag:name]] markers for real mentions of verified
    members. Ambiguous or unknown names become plain @name text (no ping) and
    are reported back so she can ask who was meant."""
    text = text or ""
    if not TAG_MARKER_RE.search(text):
        return text, [], []
    resolved, unclear = [], []

    def _sub(match):
        member, is_unclear = find_tag_target(message.guild, match.group(1))
        if member is not None:
            resolved.append(member)
            return f"<@{member.id}>"
        unclear.append(match.group(1).strip())
        return "@" + match.group(1).strip()

    out = TAG_MARKER_RE.sub(_sub, text)
    return out, resolved, unclear


TAG_UNCLEAR_LINES = [
    "ek sec - {names} clear nahi ho paya, galat banda tag nahi karna 😅 poora username batao na?",
    "ruko, {names} se mujhe sure nahi hai kaun... exact username doge? wrong person ko ping nahi karna 🥺",
]

# ---------------------------------------------------------------------------
# Nickname registry + VC summons.
# Both are handled in CODE, parsed from the CURRENT addressed message only.
# Model output, chat history, quoted text and diary/registry contents can
# never trigger a ping, a save or a cross-channel post: no stored value is
# ever used to build mention syntax, and every relay below goes out only
# after code-side verification of the people and the destination.
# ---------------------------------------------------------------------------

# "Mayank ka username mayank_123 (hai)" / "Mayank's username is mayank_123"
ALIAS_TEACH_RE = re.compile(
    r"(?P<alias>[\w][\w .-]{0,30}?)\s+ka\s+(?:discord\s+)?username\s+"
    r"[\s:=]*(?:is[\s:=]+)?(?P<username>[a-z0-9_.]{2,32})\b",
    re.IGNORECASE,
)
ALIAS_TEACH_RE2 = re.compile(
    r"(?P<alias>[\w][\w .-]{0,30}?)'s\s+(?:discord\s+)?username\s+"
    r"[\s:=]*(?:is[\s:=]+)?(?P<username>[a-z0-9_.]{2,32})\b",
    re.IGNORECASE,
)
# "call me X" / "mujhe X bulao (bulana/kehte ho/kehna)"
SELF_ALIAS_RE = re.compile(
    r"\bcall\s+me\s+(?P<alias>[\w][\w .-]{0,30}?)\s*[.!]?$", re.IGNORECASE)
SELF_ALIAS_RE2 = re.compile(
    r"\bmujhe\s+(?P<alias>[\w][\w .-]{0,30}?)\s+"
    r"(?:bula\w*|kehti\s+ho|kehte\s+ho|kehna)\s*[.!]?$", re.IGNORECASE)
# "Mayank ko bhool ja" / "forget the nickname Mayank"
ALIAS_FORGET_RE = re.compile(
    r"(?P<alias>[\w][\w .-]{0,30}?)\s+ko\s+bhool\s+ja\w*\s*[.!]?$", re.IGNORECASE)
ALIAS_FORGET_RE2 = re.compile(
    r"\bforget\s+(?:the\s+)?(?:name|alias|nickname)\s+"
    r"(?P<alias>[\w][\w .-]{0,30}?)\s*[.!]?$", re.IGNORECASE)

# leading filler words that can get swept into a captured nickname
_ALIAS_FILLERS = {
    "suno", "haan", "han", "ok", "okay", "accha", "acha", "are", "arre",
    "toh", "to", "aur", "please", "pls", "plz", "yaar", "bro", "matlab",
    "ki", "ab", "bas", "ek", "kaam", "karo", "kar", "bolo", "note",
    "yaad", "rakhna", "rakho", "rakh", "remember", "likh", "likho", "lo",
}
# words that make a "call me ..." capture clearly not a name
_NOT_NAME_WORDS = {
    "when", "if", "after", "back", "later", "kal", "jab", "mat", "nahi",
    "nhi", "dont", "don't", "please", "pls", "kab", "phir", "se", "kuch",
}
# aliases that would be ambiguous or abusive as names
ALIAS_RESERVED_WORDS = {
    "everyone", "here", "someone", "all", "koi", "sab", "sabko", "sabhi",
    "channel", "server", "admin", "mod",
}
# never parsed as a nickname in "X ko bhool ja" - these are people-talk, not
# names, and diary-forget phrases ("mujhe bhool ja") must fall through to the
# diary handler (which is checked earlier anyway)
ALIAS_FORGET_SKIP = {"mujhe", "hum", "humein", "unko", "usko", "inko", "isko", "sabko", "sab", "inhe", "unhe"}


def action_text(message, content):
    """Only a direct current human request is eligible for side effects.

    Quotes, code, forwarded snapshots, reported speech, questions about a
    command and negated commands are deliberately not action syntax.
    This conservative parser may ask people to restate an unusual request.
    """
    if getattr(message.author, "bot", False) or getattr(message, "webhook_id", None):
        return None
    if getattr(message, "message_snapshots", None):
        return None
    if re.search(r"[`\"“”]|(?:^|\n)\s*>|https?://", content or ""):
        return None
    if re.search(r"\b(example|quote|quoted|said|says|bola|bolta|kehta|forwarded|agar|if|don't|dont|not|mat|nahi|nhi)\b|[?]", content or "", re.I):
        return None
    return _addressed_text(content)


def _addressed_text(content):
    text = re.sub(rf"^\s*{re.escape(BOT_NAME)}\b[,:! ]*", "", content or "", flags=re.I)
    if client.user:
        text = re.sub(rf"^\s*<@!?{client.user.id}>[,:! ]*", "", text)
    return text.strip()


def _strip_fillers(alias):
    words = [w for w in (alias or "").split() if w.casefold() not in _ALIAS_FILLERS]
    return " ".join(words[-3:])  # a nickname is at most a few words


def _plausible_self_name(alias):
    words = (alias or "").split()
    return bool(alias) and len(words) <= 3 and not any(
        w.casefold() in _NOT_NAME_WORDS for w in words)


def alias_reserved_reason(guild, alias, member):
    """Why this alias may not point at member, else None."""
    a = (alias or "").casefold()
    if not a:
        return "ye naam theek nahi lag raha 😅 chhota sa simple naam do"
    if a in ALIAS_RESERVED_WORDS or a == BOT_NAME.casefold():
        return f'"{alias}" toh common word hai, isse confusion hoga 😅 koi doosra nickname do'
    for m in list(guild.members):
        if m.id == member.id:
            continue
        if a in {m.name.casefold(), m.display_name.casefold(), (m.global_name or "").casefold()}:
            return (f'"{alias}" toh {m.display_name} ka apna naam hai - main usse hijack nahi kar sakti, '
                    "doosra naam batao 😅")
    return None


async def _alias_reply(message, text):
    try:
        await message.reply(text, mention_author=False, allowed_mentions=reply_mentions(message))
    except Exception:
        log.exception("Failed to send alias reply in channel %s", message.channel.id)


async def handle_alias_teach(message, content):
    """'X ka username Y' -> remember X as a server nickname for member Y."""
    content = action_text(message, content)
    if content is None:
        return False
    content = re.sub(r"^(?:yaad rakhna|remember|note karlo)\s+", "", content, flags=re.I)
    content = re.sub(r"\s+hai[.!]?$", "", content, flags=re.I).rstrip(".!")
    m = ALIAS_TEACH_RE.fullmatch(content) or ALIAS_TEACH_RE2.fullmatch(content)
    if not m:
        return False
    alias = aliases.clean_alias(_strip_fillers(m.group("alias")))
    username = m.group("username")
    if not alias:
        await _alias_reply(message, "wo naam theek nahi lag raha 😅 chhota sa simple naam do")
        return True
    un = username.casefold()
    matches = [mm for mm in list(message.guild.members) if mm.name.casefold() == un]
    if len(matches) != 1:
        await _alias_reply(
            message,
            f'"{username}" naam ka member is server mein clearly nahi mila - exact username dobara check karke batao? 😅')
        return True
    member = matches[0]
    reason = alias_reserved_reason(message.guild, alias, member)
    if reason:
        await _alias_reply(message, reason)
        return True
    status, existing_id = aliases.set_alias(message.guild.id, alias, member.id, by_id=message.author.id)
    if status == "taken":
        existing = message.guild.get_member(existing_id)
        who = existing.display_name if existing else "kisi aur"
        await _alias_reply(
            message,
            f'"{alias}" toh already {who} ke liye saved hai - galat banda tag na ho isliye pehle '
            f'"{alias} ko bhool ja" bolke hatana padega 🙈')
        return True
    if status in {"error", "invalid"}:
        await _alias_reply(message, "abhi nickname save nahi hua, dobara try karna")
        return True
    if status == "full":
        await _alias_reply(message, "meri nickname list full hai is server ki 😥 pehle koi purana naam bhulwao")
        return True
    await _alias_reply(message, f'likh liya 📔 ab "{alias}" bolne par {member.display_name} ko tag kar dungi 💗')
    return True


async def handle_self_alias(message, content):
    """'call me X' / 'mujhe X bulao' -> nickname for the speaker themself."""
    content = action_text(message, content)
    if content is None:
        return False
    m = SELF_ALIAS_RE.search(content) or SELF_ALIAS_RE2.search(content)
    if not m:
        return False
    alias = aliases.clean_alias(m.group("alias"))
    if not _plausible_self_name(alias):
        return False
    reason = alias_reserved_reason(message.guild, alias, message.author)
    if reason:
        await _alias_reply(message, reason)
        return True
    status, existing_id = aliases.set_alias(message.guild.id, alias, message.author.id, by_id=message.author.id)
    if status == "taken":
        await _alias_reply(
            message,
            f'"{alias}" toh already kisi aur ka nickname hai 🙈 koi doosra naam choose karo, '
            f'ya pehle "{alias} ko bhool ja" bulwao jiska hai')
        return True
    if status in {"error", "invalid"}:
        await _alias_reply(message, "abhi nickname save nahi hua, dobara try karna")
        return True
    if status == "full":
        await _alias_reply(message, "meri nickname list full hai is server ki 😥 pehle koi purana naam bhulwao")
        return True
    await _alias_reply(message, f'theek hai, ab tumhe "{alias}" se bhi tag kar sakti hain 💗')
    return True


async def handle_alias_forget(message, content):
    """'X ko bhool ja' / 'forget the nickname X' -> drop a taught nickname."""
    content = action_text(message, content)
    if content is None:
        return False
    m = ALIAS_FORGET_RE.fullmatch(content) or ALIAS_FORGET_RE2.fullmatch(content)
    if not m:
        return False
    alias = aliases.clean_alias(m.group("alias"))
    if not alias or alias.casefold() in ALIAS_FORGET_SKIP:
        return False
    if aliases.remove_alias(message.guild.id, alias):
        await _alias_reply(message, f'ho gaya, ab "{alias}" kisi ko tag nahi karega 👍')
    else:
        await _alias_reply(message, f'"{alias}" toh meri nickname list mein hai hi nahi 😅')
    return True


# ---- VC summons ------------------------------------------------------------

# "bulao / bolo / bhejo / summon / tell them / vc join karo ..." - the verb
# that makes a #channel mention a request to call people, not just talk
SUMMON_VERB_RE = re.compile(
    r"(\bbula\w*|\bbolo\b|\bbolna\b|\bbol\s+do\b"
    r"|\bbhej\w*|\bsummon\b|\binvite\b"
    r"|\bcall\s+(?:them|him|her|in|sab)\b|\btell\s+(?:them|sab)\b"
    r"|\baao\b|\baajao\b|\baa\s+jao\b"
    r"|\bvc\s+join\b|\bjoin\s+kar\w*)",
    re.IGNORECASE,
)

CHANNEL_MENTION_RE = re.compile(r"<#\d+>")
USER_MENTION_RE = re.compile(r"<@!?\d+>")

SUMMON_STOPWORDS = {
    "vc", "voice", "chat", "join", "karna", "karno", "karne", "karo", "kar",
    "kardo", "kare", "kariye", "bolo", "bol", "bolna", "boldo", "bulao",
    "bula", "bulana", "bulaao", "bulwa", "bulwao", "bhejo", "bhej", "bhejna",
    "bhejdo", "turant", "abhi", "ab", "jaldi", "jald", "aao", "aajao", "aa",
    "jao", "ja", "summon", "call", "tell", "ask", "invite", "please", "pls",
    "plz", "ko", "ka", "ki", "ke", "mein", "me", "pe", "par", "per", "se",
    "tak", "to", "in", "on", "at", "the", "is", "ye", "yeh", "wo", "woh",
    "unko", "usko", "inko", "isko", "unhe", "sab", "sabko", "sabhi", "logo",
    "log", "guys", "dena", "do", "dijiye", "zara", "ek", "baar", "msg",
    "message", "ping", "mention", "tag", "fast", "quickly", "immediately",
    "raha", "rahi", "rahe", "hai", "h", "ho", "un", "yahan", "wahan",
    "idhar", "udhar", "hi", "bhi", "na", "thoda", "sabse", "us",
    "channel", "group", "de", "liye", "hue", "huye", "gaya", "gaye",
}

MAX_SUMMON_PINGS = 5        # hard cap on people pinged per summon
MAX_SUMMON_CANDIDATES = 8   # parsed name chunks allowed before we ask instead
SUMMON_COOLDOWN = 45        # seconds between one member's summons per server
_last_summon = {}           # (guild_id, user_id) -> monotonic time


def extract_name_candidates(content):
    """Pull the named people out of a summon request.

    Markup (<#channel>, <@member>) and her own name are removed first; the
    rest is split on separators / 'aur' / 'and', and what survives the
    stopword filter is a candidate name to verify against the member list.
    """
    text = CHANNEL_MENTION_RE.sub(" ", content or "")
    text = USER_MENTION_RE.sub(" ", text)
    text = _addressed_text(text)
    text = text.replace("#", " ")
    candidates = []
    for part in re.split(r"[,;&+\n/]|\baur\b|\band\b", text, flags=re.IGNORECASE):
        words = [w for w in re.split(r"\s+", part.strip())
                 if w and w.casefold() not in SUMMON_STOPWORDS]
        name = " ".join(words).strip(" .!?,:\"'()[]{}")
        if len(name) >= 2 and re.search(r"\w", name, re.UNICODE):
            candidates.append(name)
    return candidates


def build_summon_text(requester, members):
    """The fixed, code-built summons. No model text, no stored free text."""
    mentions = " ".join(f"<@{m.id}>" for m in members)
    vchan = getattr(getattr(requester, "voice", None), "channel", None)
    vname = getattr(vchan, "name", "") if vchan else ""
    where = f" ({vname} VC)" if vname else ""
    return (f"{mentions} abhi ke abhi VC join kariye turant! 🎮\n"
            f"- {requester.display_name} bula rahe hain{where}")


async def _summon_reply(message, text):
    try:
        await message.reply(text, mention_author=False, allowed_mentions=reply_mentions(message))
    except Exception:
        log.exception("Failed to send summon reply in channel %s", message.channel.id)


async def handle_summon(message, content):
    """Relay a VC summon to the one #channel the requester natively mentioned.

    Every failure mode asks or refuses IN the current channel and sends
    nothing: an unclear name, a missing name, an unreachable destination,
    too many people. A partial summons never goes out.
    """
    content = action_text(message, content)
    if content is None:
        return
    guild = message.guild
    author = message.author
    mentioned = list(message.channel_mentions)
    if len(mentioned) != 1:
        await _summon_reply(message, "ek hi #channel mention karo na, itne mein se kahan bheju samajh nahi aaya 😅")
        return
    dest = mentioned[0]
    # defensive: Discord's autocomplete only offers this server's channels,
    # but never relay anywhere without verifying the destination's guild
    if getattr(dest, "guild", None) is None or dest.guild.id != guild.id:
        await _summon_reply(message, "us channel mein nahi bhej sakti - woh is server ka nahi hai 🙈")
        return
    if getattr(dest, "type", None) is not discord.ChannelType.text:
        await _summon_reply(message, "wahan text nahi bhej sakti 😅 kisi text channel ko # mention karo")
        return
    me = guild.me
    if me is None:
        await _summon_reply(message, "meri server permissions verify nahi hui, abhi nahi bhejungi")
        return
    if me is not None:
        bot_perms = dest.permissions_for(me)
        if not (bot_perms.view_channel and bot_perms.send_messages):
            await _summon_reply(message, f"{dest.mention} mein meri posting permission nahi hai 🥺 admin se settings check karwa do")
            return
    req_perms = dest.permissions_for(author)
    if not (req_perms.view_channel and req_perms.send_messages):
        await _summon_reply(message, "tum khud us channel ko dekh nahi sakte, wahan summon nahi bhejungi 🙈")
        return

    # people: native @mentions arrive already verified against the server
    members, seen = [], set()
    for raw_member in message.mentions:
        m = guild.get_member(raw_member.id)
        if m is None:
            await _summon_reply(message, "ek member is server mein verify nahi hua, kuch nahi bheja")
            return
        if m.bot or (client.user and m.id == client.user.id):
            continue
        if m.id not in seen:
            seen.add(m.id)
            members.append(m)
    unclear = []
    for name in extract_name_candidates(content):
        member, _unclear = find_tag_target(guild, name)
        if member is not None and not member.bot:
            if member.id not in seen:
                seen.add(member.id)
                members.append(member)
        else:
            unclear.append(name)
    if unclear:
        names = ", ".join(f'"{n}"' for n in unclear[:5])
        await _summon_reply(
            message,
            f"ruko, {names} clear nahi ho paye - kuch bhejne se pehle pooch leti hoon: inke exact usernames batao? "
            "galat bande ko ping nahi karna 😅")
        return
    if not members:
        await _summon_reply(message, "kise bulaana hai? naam batao (ya @mention kar do) 😊")
        return
    if len(members) > MAX_SUMMON_PINGS:
        await _summon_reply(message, f"ek saath itne nahi bula paungi (max {MAX_SUMMON_PINGS}) - thode kam karke bolo 😅")
        return

    key = (guild.id, author.id)
    if time.monotonic() - _last_summon.get(key, -1e9) < SUMMON_COOLDOWN:
        await _summon_reply(message, "ek sec, abhi-abhi ek summon bheja hai - thodi der ruk jao 🙏")
        return

    try:
        await dest.send(
            build_summon_text(author, members),
            allowed_mentions=discord.AllowedMentions(users=members, everyone=False, roles=False),
        )
    except Exception:
        log.exception("Summon relay failed to channel %s", dest.id)
        await _summon_reply(message, "bhejne mein error aa gaya 😢 thodi der baad phir try karna")
        return
    _last_summon[key] = time.monotonic()
    await _summon_reply(message, f"bhej diya {dest.mention} mein ✅")


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
    session = await groq_session()
    return await retrying.post_json(session, url, label="Gemini",
        headers={"x-goog-api-key": GEMINI_API_KEY}, payload=payload)


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
    data = await retrying.post_json(session, GROQ_CHAT_URL, label="Groq(extractor)",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, payload=payload)
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
    attempts = 1  # network retries share one independent three-call budget
    ok = False
    try:
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(SAVE_RETRY_DELAY)
            try:
                mem = diary.load(user_id)
                async with retrying.request_scope():
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
                            attempt + 1, attempts, user_id, type(e).__name__)
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
    messages = [{"role": "system", "content": with_diary(SYSTEM_PROMPT, diary_block) + current_context() + SEARCH_CAPABILITY + MODEL_SEARCH_CAPABILITY + extra_system + PING_GUIDANCE}]
    for author, _author_id, content in history[channel_id]:
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
    data = await retrying.post_json(session, GROQ_CHAT_URL, label="Groq", headers=headers, payload=payload)
    return (data["choices"][0]["message"]["content"] or "").strip()

async def think_gemini(channel_id, diary_block="", image_parts=None, extra_system=""):
    """Main brain: ask Gemini (generateContent REST) for Kristy's next line."""
    contents = []
    for author, _author_id, content in history[channel_id]:
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
        "system_instruction": {"parts": [{"text": with_diary(SYSTEM_PROMPT, diary_block) + current_context() + SEARCH_CAPABILITY + MODEL_SEARCH_CAPABILITY + extra_system + PING_GUIDANCE}]},
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
            "Gemini returned no text (finishReason=%s)",
            (candidate or {}).get("finishReason"),
        )
    return reply_text

@retrying.operation
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
    data = await retrying.post_json(session, TAVILY_SEARCH_URL, label="Tavily", headers=headers, payload=payload)
    answer = (data.get("answer") or "").strip()
    lines = []
    if answer:
        lines.append(f"Quick answer: {answer}")
    for r in (data.get("results") or [])[:6]:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip()
        snippet = (r.get("content") or "").strip()
        url = (r.get("url") or "").strip()
        if len(snippet) < MIN_RESULT_CONTENT_CHARS:
            # boilerplate (channel blurbs, link dumps, nav text) is not
            # evidence; drop it whatever the domain, keep substantive pages
            continue
        snippet = snippet[:MAX_RESULT_SNIPPET_CHARS]
        if title or snippet:
            lines.append(f"- {title}: {snippet} ({url})".strip())
    digest = "\n".join(lines).strip()
    if not digest:
        raise RuntimeError("search returned no usable results")
    return digest[:SEARCH_DIGEST_CHARS]

def latest_question(channel_id):
    """The most recent non-Kristy message in this channel (the search query)."""
    for author, _author_id, content in reversed(history[channel_id]):
        if author != BOT_NAME:
            return content
    return ""

async def think_search(channel_id, diary_block="", extra_system="", image_parts=None,
                       requester_id=None, also_author_id=None):
    """Use live web results, then answer in persona; errors are handled by caller."""
    query, question = search_query_for(channel_id, requester_id, also_author_id)
    if not query:
        raise RuntimeError("nothing to search for")
    digest = await fetch_search_results(query)
    search_block = (
        f"{SEARCH_HINT}\n\nMember's actual question: \"{question}\"\n"
        f"(search query used: \"{query}\")\nWeb search results:\n{digest}"
    )
    return await think(channel_id, diary_block, image_parts=image_parts,
                       extra_system=extra_system + search_block)

async def maybe_model_search(channel_id, reply, diary_block, extra="", image_parts=None):
    """Let the brain ask for ONE live search per reply via a [[search: q]] marker.

    Returns the final reply to send. No marker -> reply unchanged (zero extra
    API calls, so ordinary chat is untouched). With a marker: run the search
    (respecting the daily cap), then make a second brain call with the results
    (or with a transparent failure note) so she answers grounded - and can
    never claim she searched when no search actually happened.
    """
    m = MODEL_SEARCH_RE.search(reply or "")
    if not m:
        return reply
    query = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    log.info("Model-driven search requested: %s", query)
    if search_quota_left():
        try:
            digest = await fetch_search_results(query)
            mark_search_used()  # only successful searches spend the daily cap
            note = (f'{MODEL_SEARCH_HINT}\n\n(search query used: "{query}")\n'
                    f"Web search results:\n{digest}")
        except Exception as e:
            log.warning("Model-driven search failed: %s", e)
            note = MODEL_SEARCH_FAIL_NOTE
    else:
        note = MODEL_SEARCH_FAIL_NOTE
    try:
        followup = await think(channel_id, diary_block, image_parts=image_parts,
                               extra_system=extra + note)
    except Exception as e:
        log.warning("Model search follow-up brain call failed: %s", e)
        followup = ""
    followup = MODEL_SEARCH_RE.sub("", followup or "").strip()
    if followup:
        return followup
    # The follow-up brain call gave nothing: stay transparent, never guess.
    question = latest_question(channel_id)
    if re.search(r"\b(karo|kar|batao|bata|kya|hai|hain|mein|ka|ki|nahi|kyu)\b", question, re.I):
        return "abhi search nahi ho paya, isliye main guess nahi karungi 🥺 thodi der baad phir pooch na."
    return "i couldn't search just now, so i don't want to guess. try again in a bit?"

@retrying.operation
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
            log.warning("Gemini brain failed, falling back to Groq: %s", type(e).__name__)
    return await think_groq(channel_id, diary_block, extra_system)

@client.event
async def on_ready():
    log.info("%s is online as %s", BOT_NAME, client.user)
    await client.change_presence(activity=discord.Game(name=f"Genshin Impact | @{BOT_NAME} to chat"))
    announcements.start(client)
    if client.music:
        await client.music.ready()

@client.event
async def on_voice_state_update(member, before, after):
    if client.music:
        await client.music.voice_changed(member, before, after)

@client.event
async def on_disconnect():
    if client.music:
        await client.music.gateway_disconnected()

def is_bot_owner(author):
    """Check the configured owner ID, or use the documented name fallback."""
    if OWNER_ID:
        return str(getattr(author, "id", "")) == OWNER_ID
    names = (getattr(author, "name", ""), getattr(author, "display_name", ""))
    return any(str(name).strip().casefold() == "fazallette" for name in names if name)


def format_server_list(guilds, include_owner_id_note=False):
    """Build the plain-text owner response without invoking an AI model."""
    guilds = list(guilds)
    lines = [f"I'm in {len(guilds)} server(s):"]
    if guilds:
        for guild in guilds:
            members = getattr(guild, "member_count", None)
            member_text = str(members) if members is not None else "unknown"
            lines.append(f"- {guild.name} ({member_text} members)")
    else:
        lines.append("- No servers yet.")
    if include_owner_id_note:
        lines.extend(("", "Security note: set OWNER_ID to your Discord user ID in .env; the name fallback can be spoofed."))
    return "\n".join(lines)


_owner_id_fallback_notice_sent = False


@client.event
async def on_message(message):
    global _owner_id_fallback_notice_sent

    # Owner utility commands work in DMs as well as server channels.
    if message.author.bot or getattr(message, "webhook_id", None):
        return
    content = message.content.strip()
    sticker_note = describe_stickers(message)
    if sticker_note:
        content = f"{content} {sticker_note}".strip()
    if content.casefold() == "!servers":
        if not is_bot_owner(message.author):
            return
        show_owner_id_note = not OWNER_ID and not _owner_id_fallback_notice_sent
        if show_owner_id_note:
            _owner_id_fallback_notice_sent = True
        await message.reply(format_server_list(client.guilds, show_owner_id_note), mention_author=False)
        return

    # Kristy otherwise only responds in server channels.
    if not message.guild:
        return

    if content:
        history[message.channel.id].append((message.author.display_name, message.author.id, content[:300]))

    if not should_respond(message):
        return

    music_context = client.music.context(message.channel) if client.music else ""
    user_id = message.author.id
    speaker = message.author.display_name

    has_image = message_has_image(message)
    if has_image and not content:
        # image-only message: give her something to react to
        content = IMAGE_ONLY_PROMPT
        history[message.channel.id].append((speaker, user_id, content))
    if not content:
        # Any other contentless trigger (a poll, a file with no caption, ...)
        # must still add a user turn: without one the history ends on her own
        # reply and Gemini rejects the request with a 400.
        content = EMPTY_MESSAGE_PROMPT
        history[message.channel.id].append((speaker, user_id, content))

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

    # Nickname teaching / forgetting and VC summons run in code only, on the
    # current addressed message. They must never depend on model output, so
    # they are checked before the brain and return without any AI call.
    if await handle_alias_forget(message, content):
        return
    if await handle_alias_teach(message, content):
        return
    if await handle_self_alias(message, content):
        return
    if action_text(message, content) is not None and re.search(r"\b(vc|voice)\b", content, re.I) and SUMMON_VERB_RE.search(content):
        await handle_summon(message, content)
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
            if client.music:
                if music_context:
                    extra = (extra + "\n" + music_context).strip()

            async with retrying.request_scope(), message.channel.typing():
                image_parts = []
                if GEMINI_API_KEY and has_image:  # Groq fallback: silently text-only
                    try:
                        image_parts = await collect_image_parts(message)
                    except Exception as e:
                        log.warning("Image collection failed: %s", e)
                        image_parts = []
                try:
                    if looks_like_search(content):
                        search_error = None
                        ref_author_id = None
                        ref = message.reference
                        if (ref and isinstance(ref.resolved, discord.Message)
                                and ref.resolved.author != client.user
                                and not ref.resolved.author.bot):
                            # explicit reply to someone's message: that
                            # author's messages are fair walk-back targets
                            ref_author_id = ref.resolved.author.id
                        query, _question = search_query_for(
                            message.channel.id, user_id, ref_author_id)
                        if not query:
                            # command-only request with nothing of their own to
                            # walk back to: ask, never search a neighbour's chat
                            reply = "kis baare mein search karu? thoda specific bata do 🥺"
                        elif not query_has_anchor(query) and len(query.split()) <= 1:
                            # a lone unknown word (name we don't know, typo)
                            # would return off-topic junk; clarify instead
                            reply = ("ye kis game ya cheez ke baare mein hai? "
                                     "thoda aur bata do, phir dhoondh ke batati hu 🥺")
                        elif not search_quota_left():
                            search_error = "daily search limit reached"
                        else:
                            try:
                                reply = await think_search(message.channel.id, diary_block, extra,
                                                           image_parts=image_parts, requester_id=user_id,
                                                           also_author_id=ref_author_id)
                                if not reply:
                                    raise RuntimeError("search returned no text")
                                mark_search_used()  # only successful searches spend the cap
                            except Exception as e:
                                search_error = "web search failed"
                                log.warning("Web search failed: %s", type(e).__name__)
                        if search_error:
                            # Never silently fall back to an answer that could be
                            # mistaken for researched current information.
                            if re.search(r"\b(search|google|look\s+up)\b", content, re.I):
                                reply = "i couldn't search just now, so i don't want to guess about that."
                            elif re.search(r"\b(karo|kar|batao|bata|kya|hai|hain|mein|ka|ki)\b", content, re.I):
                                reply = "abhi search nahi ho paya, isliye current info guess nahi karungi."
                            else:
                                reply = "i couldn't search just now, so i don't want to guess about current info."
                            log.warning("Replying transparently: %s", search_error)
                    else:
                        reply = await think(message.channel.id, diary_block, image_parts, extra)
                        reply = await maybe_model_search(message.channel.id, reply, diary_block, extra, image_parts)
                except Exception as e:
                    log.warning("AI brain error: %s", type(e).__name__)
                    reply = random.choice(FALLBACK_LINES)
                reply = MODEL_SEARCH_RE.sub("", reply or "").strip()
                reaction, reply = extract_reaction(reply)
                if not reply:
                    reply = EMPTY_REPLY_FALLBACK

    ping_requested, reply = extract_ping_request(reply)
    reply = sanitize_mentions(reply) or EMPTY_REPLY_FALLBACK
    # only now, after model-written mention syntax is stripped, reinsert real
    # mentions for verified [[tag:...]] targets; unclear names get a
    # clarification ask instead of a guessed ping
    if action_text(message, content) is None:
        reply = TAG_MARKER_RE.sub(lambda m: m.group(1), reply)
    reply, tag_members, tag_unclear = resolve_tag_requests(message, reply)
    if tag_unclear:
        names = ", ".join(f'"{n}"' for n in tag_unclear)
        reply = (reply + "\n\n" + random.choice(TAG_UNCLEAR_LINES).format(names=names)).strip()
    # history keeps her reply for context, bounded so long chunked answers
    # cannot eat the brain's input-token headroom; the full text already
    # lives in the channel for anyone to scroll back to
    stored_reply = reply[:HISTORY_REPLY_CHARS] + (f" [sent a {gif_desc} gif]" if gif_desc else "")
    history[message.channel.id].append((BOT_NAME, None, stored_reply))
    try:
        # long answers go out as sequential messages at natural boundaries;
        # the ping and the reaction stay on the first message only
        chunks = chunk_reply(reply)
        first = f"<@{message.author.id}> {chunks[0]}" if ping_requested else chunks[0]
        await message.reply(
            first,
            mention_author=False,
            allowed_mentions=reply_mentions(message, extra_users=tag_members),
        )
        for extra_chunk in chunks[1:]:
            await message.channel.send(extra_chunk)
        if gif_url:
            await message.channel.send(gif_url)  # Discord auto-embeds direct GIF links
        await try_reaction(message, reaction)
    except Exception:
        log.exception("Failed to send reply in channel %s", message.channel.id)

    # learn from this exchange in the background, after the reply is already out
    if content and reply not in FALLBACK_LINES and reply != EMPTY_REPLY_FALLBACK:
        buf = _pending.setdefault(user_id, [])
        if music_context:
            from music.memory import durable_exchange
            buf.extend(durable_exchange(speaker, content, BOT_NAME, reply, music_context))
        else:
            buf.append((speaker, content[:300]))
            buf.append((BOT_NAME, reply[:300]))
        del buf[:-12]  # keep at most the last 6 exchanges
        maybe_schedule_extraction(user_id, speaker, content, message.channel)

if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
