"""
Proactive announcements for Kristy: daily good-morning post, new redeem-code
alerts and limited-time event announcements for one Discord server.

Everything here is config-by-env with safe defaults for Fazal's server:

  ANNOUNCE_ENABLED        master switch, default 1 (set 0 to silence all)
  ANNOUNCE_DRY_RUN        1 = fetch + log what WOULD be posted, send nothing
  MORNING_CHANNEL_ID      default 1438772091116847246 (chat channel)
  MORNING_TIME            local time, default 09:00
  MORNING_TZ              IANA zone, default Asia/Kolkata
  CODES_CHANNEL_ID        default 1438772091116847247 (clips & highlights)
  EVENTS_CHANNEL_ID       default = CODES_CHANNEL_ID
  ANNOUNCE_CHECK_MINUTES  codes/events poll cadence, default 60
  OFFICIAL_UIDS           comma HoYoLAB uids treated as official, default 1015537
  ANNOUNCE_CODES_MODE     off (default) | strict | community
                            off:       codes feature disabled
                            strict:    post only with fresh-release evidence in an
                                       official HoYoLAB post body (rarely fires;
                                       HoYoverse barely posts codes on HoYoLAB)
                            community: also auto-post game8-listed codes whose
                                       Date Added is fresh, with a source footer

State lives in MEMORY_DIR (default ./memory, gitignored):
  announcements_state.json (+ .bak, .marker). Seen sets are append-only and
  never reset on parse failures. Corrupt established state disables posting
  loudly instead of re-seeding and re-pinging old codes.
"""

import os
import re
import io
import json
import shutil
import asyncio
import logging
import datetime

import aiohttp
import discord

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# Termux Python ships without IANA tzdata, so ZoneInfo("Asia/Kolkata") fails
# there. Fixed-offset fallback for the zones we actually use; anything else
# falls back to system local time (logged once at startup, not per loop tick).
_TZ_FALLBACK_OFFSETS = {
    "asia/kolkata": datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST"),
}

from gifs import GIFS
import random

log = logging.getLogger("kristy.announce")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def _flag(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")

ANNOUNCE_ENABLED = _flag("ANNOUNCE_ENABLED", "1")
DRY_RUN = _flag("ANNOUNCE_DRY_RUN", "0")
MORNING_CHANNEL_ID = os.getenv("MORNING_CHANNEL_ID", "1438772091116847246").strip()
CODES_CHANNEL_ID = os.getenv("CODES_CHANNEL_ID", "1438772091116847247").strip()
EVENTS_CHANNEL_ID = os.getenv("EVENTS_CHANNEL_ID", CODES_CHANNEL_ID).strip()
MORNING_TIME = os.getenv("MORNING_TIME", "09:00").strip()
MORNING_TZ = os.getenv("MORNING_TZ", "Asia/Kolkata").strip()
CODES_MODE = os.getenv("ANNOUNCE_CODES_MODE", "community").strip().lower()
WELCOME_ENABLED = _flag("WELCOME_ENABLED", "1")
WELCOME_CHANNEL_ID = os.getenv("WELCOME_CHANNEL_ID", "1438772091116847246").strip()
try:
    CHECK_MINUTES = max(15, int(os.getenv("ANNOUNCE_CHECK_MINUTES", "60")))
except ValueError:
    CHECK_MINUTES = 60
OFFICIAL_UIDS = {u.strip() for u in os.getenv("OFFICIAL_UIDS", "1015537").split(",") if u.strip()}

MEMORY_DIR = os.getenv("MEMORY_DIR", "./memory")
STATE_PATH = os.path.join(MEMORY_DIR, "announcements_state.json")
STATE_BAK = STATE_PATH + ".bak"
STATE_MARKER = STATE_PATH + ".marker"

GAME8_CODES_URL = "https://game8.co/games/Genshin-Impact/archives/304759"
REDEEM_URL = "https://genshin.hoyoverse.com/en/gift?code="
NEWS_LIST_URL = "https://bbs-api-os.hoyolab.com/community/post/wapi/getNewsList"
POST_FULL_URL = "https://bbs-api-os.hoyolab.com/community/post/wapi/getPostFull"
ARTICLE_URL = "https://www.hoyolab.com/article/"
IMG_HOST_ALLOWED = "upload-os-bbs.hoyolab.com"   # the PUBLIC image host; the other one 403s

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
HOYO_HEADERS = {"x-rpc-language": "en-us"}

# Morning post fires only inside this window after MORNING_TIME; if the bot is
# offline at MORNING_TIME the day's greeting is skipped (approved policy: no
# late catch-up pings).
MORNING_WINDOW_MINUTES = 10

# Codes: conservative fresh-release phrases for strict mode (ambiguous -> hold)
FRESH_PHRASES = (
    "new redemption code", "redemption code", "special program", "new code",
)
# Community mode: game8 "Date Added" within this many days counts as fresh
COMMUNITY_FRESH_DAYS = 2
# Sanity floor for the one-time baseline seed; fewer codes than this means the
# page layout changed and we treat the parse as failed (posting stays off).
SEED_MIN_CODES = 20

# Events: skip community chatter, only real events reach the channel
EVENT_EXCLUDE_WORDS = ("discussion room", "giveaway", "top-up", "commenting", "chance to win")
MIN_EVENT_IMAGE_BYTES = 20 * 1024   # smaller than this is an icon, not a banner
MAX_EVENT_IMAGE_BYTES = 15 * 1024 * 1024

_everyone_mentions = discord.AllowedMentions(everyone=True, users=False, roles=False)

# --------------------------------------------------------------------------
# State (append-only, fail-closed)
# --------------------------------------------------------------------------

_state = None
_state_broken = False   # corrupt established state -> never post

def _default_state():
    return {
        "seeded": False,
        "seen_codes": [],
        "expired_codes": [],
        "seen_posts": [],      # official news post_ids already fetched for code evidence
        "seen_events": [],     # type-2 post_ids already evaluated
        "held_codes": {},      # code -> {"first_seen": iso, "date_added": str|None}
        "morning_last": "",    # YYYY-MM-DD of last posted morning message
        "feeds_seeded": False,  # True once events/posts baseline is recorded
    }

def load_state():
    """Fail-closed: corrupt established state disables posting, no reseed."""
    global _state, _state_broken
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                _state = {**_default_state(), **json.load(f)}
            return
        except Exception:
            log.error("announcements: state file corrupt, trying backup")
            try:
                with open(STATE_BAK, encoding="utf-8") as f:
                    _state = {**_default_state(), **json.load(f)}
                log.info("announcements: recovered state from backup")
                return
            except Exception:
                _state = _default_state()
                _state_broken = True
                log.error("announcements: state AND backup corrupt; posting DISABLED (no reseed, no reposts)")
                return
    if os.path.exists(STATE_MARKER):
        # state existed before but the file is gone: do NOT start fresh
        _state = _default_state()
        _state_broken = True
        log.error("announcements: state file missing but marker exists; posting DISABLED (no reseed, no reposts)")
        return
    _state = _default_state()  # genuine first-ever run

def save_state():
    os.makedirs(MEMORY_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)
    try:
        shutil.copyfile(STATE_PATH, STATE_BAK)
        with open(STATE_MARKER, "w") as f:
            f.write("initialized\n")
    except Exception as e:
        log.warning("announcements: backup/marker write failed: %s", e)

# --------------------------------------------------------------------------
# Fetch helpers
# --------------------------------------------------------------------------

_session = None

async def http():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(headers=UA, timeout=aiohttp.ClientTimeout(total=30))
    return _session

async def fetch_text(url, headers=None):
    s = await http()
    async with s.get(url, headers=headers or {}) as r:
        if r.status != 200:
            log.warning("announcements: GET %s -> %s", url, r.status)
            return None
        return await r.text()

async def fetch_json(url, params=None, headers=None):
    s = await http()
    async with s.get(url, params=params or {}, headers=headers or {}) as r:
        if r.status != 200:
            log.warning("announcements: GET %s -> %s", url, r.status)
            return None
        return await r.json(content_type=None)

async def fetch_bytes(url, cap):
    s = await http()
    async with s.get(url) as r:
        if r.status != 200:
            return None, None
        ctype = (r.headers.get("Content-Type") or "").lower()
        data = await r.content.read(cap + 1)
        if len(data) > cap:
            return None, None
        return data, ctype

# --------------------------------------------------------------------------
# game8 codes page parsing
# --------------------------------------------------------------------------

REWARD_RE = re.compile(r"([A-Za-z][A-Za-z'’\- ]*?)</a>\s*x\s*([\d,]+)")
DATE_ADDED_RE = re.compile(r"Date Added</b>:\s*([\d/]+)")
CLIPBOARD_RE = re.compile(r'a-clipboard__textInput"[^>]*value="([^"]+)"')

def _rows(html):
    return re.findall(r"<tr>.*?</tr>", html, flags=re.S)

def parse_game8(html):
    """Return (active, expired) or None on structural failure.
    active: list of dicts {code, rewards, date_added, special}
    expired: list of code strings
    """
    exp_idx = html.find("Expired Genshin Impact Redeem Codes")
    if exp_idx < 0:
        return None
    active_html, expired_html = html[:exp_idx], html[exp_idx:]
    sp_idx = active_html.find("Special Program")
    sp_version = None
    if sp_idx >= 0:
        head = re.sub(r"<[^>]+>", " ", active_html[sp_idx:sp_idx + 300])
        vm = re.search(r"(\d+\.\d+)", head)
        sp_version = vm.group(1) if vm else None

    active = []
    for row in _rows(active_html):
        m = CLIPBOARD_RE.search(row)
        if not m:
            continue
        code = m.group(1).strip()
        rewards = [f"{name.strip()} x{n}" for name, n in REWARD_RE.findall(row)]
        d = DATE_ADDED_RE.search(row)
        pos = active_html.find(row)
        active.append({
            "code": code,
            "rewards": rewards,
            "date_added": d.group(1).strip() if d else None,
            "special": 0 <= sp_idx <= pos,
            "program": sp_version if 0 <= sp_idx <= pos else None,
        })

    expired = []
    for row in _rows(expired_html):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
        if not cells:
            continue
        text = re.sub(r"<[^>]+>", "", cells[0]).strip()
        if text and " " not in text and len(text) <= 30:
            expired.append(text)
    return active, expired

def _fmt_reward(reward):
    """'Primogem x60' -> ':gem: **60 Primogems**'; others -> '**Name xN**'."""
    m = re.match(r"(.+?)\s+x([\d,]+)$", reward)
    if not m:
        return f"**{reward}**"
    name, n = m.group(1).strip(), m.group(2)
    low = name.lower()
    if low.startswith("primogem"):
        return f":gem: **{n} Primogems**"
    if low == "mora":
        return f":moneybag: **{n} Mora**"
    return f"**{name} x{n}**"

def _reward_line(rewards):
    if not rewards:
        return ""
    return "\n> " + " + ".join(_fmt_reward(r) for r in rewards)

# --------------------------------------------------------------------------
# HoYoLAB helpers
# --------------------------------------------------------------------------

async def hoyo_list(feed_type):
    d = await fetch_json(NEWS_LIST_URL, params={"gids": 2, "type": feed_type, "page_size": 20},
                         headers=HOYO_HEADERS)
    if not d or d.get("retcode") != 0:
        return []
    return d.get("data", {}).get("list") or []

async def hoyo_post(post_id):
    d = await fetch_json(POST_FULL_URL, params={"post_id": post_id}, headers=HOYO_HEADERS)
    if not d or d.get("retcode") != 0:
        return None
    return d.get("data", {}).get("post", {}).get("post")

def post_texts_and_images(post):
    """Extract (text_inserts, image_urls) from structured_content."""
    raw = post.get("structured_content") or ""
    texts, images = [], []
    try:
        nodes = json.loads(raw)
    except Exception:
        nodes = []
    for node in nodes:
        ins = node.get("insert")
        if isinstance(ins, dict) and "image" in ins:
            images.append(ins["image"])
        elif isinstance(ins, str):
            t = ins.strip()
            if t:
                texts.append(t)
    if not images:
        # fallback: html body, but ONLY the public host (the other host 403s)
        for u in re.findall(r'<img src="([^"]+)"', post.get("content") or ""):
            if IMG_HOST_ALLOWED in u:
                images.append(u)
    return texts, images

def event_details(texts):
    """Pull (duration_line, summary) out of official post text inserts."""
    duration, summary = None, None
    for i, t in enumerate(texts):
        if "event duration" in t.lower():
            for j in range(i + 1, min(i + 4, len(texts))):
                cand = texts[j].strip("* \n")
                if re.search(r"\d{4}", cand):
                    duration = cand.replace("\n", " ").strip()
                    break
    for t in texts:
        clean = t.strip()
        if len(clean) >= 20 and "click to" not in clean.lower() and "event duration" not in clean.lower():
            summary = clean[:150].rstrip() + ("..." if len(clean) > 150 else "")
            break
    return duration, summary

# --------------------------------------------------------------------------
# Post builders (approved templates, verbatim)
# --------------------------------------------------------------------------

def render_normal_code(code, rewards):
    return (
        "# New Genshin Redeem Code\n"
        " @everyone\n\n"
        f":yellow_circle:  **[{code}](<{REDEEM_URL}{code}>)**\n"
        + _reward_line(rewards)
    ).rstrip()

def render_special_codes(items):
    parts = [
        "# ⚡ Genshin Special Program Codes ⚡",
        "@everyone",
        "",
        "━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for it in items:
        parts.append(f":yellow_circle: **[{it['code']}](<{REDEEM_URL}{it['code']}>)**")
        rl = _reward_line(it.get("rewards"))
        if rl:
            parts.append(rl.lstrip("\n"))
        parts.append("")
    parts += [
        "━━━━━━━━━━━━━━━━━━",
        "⏳ Redeem soon! Check the source for the expiry deadline.",
    ]
    return "\n".join(parts)

COMMUNITY_FOOTER = "\n\n*- listed by game8 community page, redeem to confirm*"

def render_event(name, summary, duration, post_id):
    # Official subjects can run to full sentences; keep the post well under
    # Discord's 2000-char message limit.
    name = (name or "").strip()
    if len(name) > 180:
        name = name[:177].rstrip() + "..."
    parts = [
        "# 🎪 New Limited-Time Event!",
        "@everyone",
        "",
        f"**{name}**",
    ]
    if summary:
        parts.append(f"> {summary}")
    if duration:
        parts.append(f"> 📅 {duration}")
    parts += ["", f"🔗 Details: {ARTICLE_URL}{post_id}"]
    return "\n".join(parts)

MORNING_TEXT = "Good morning @everyone"

# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

async def _get_channel(channel_id, label):
    ch = client_ref.get_channel(int(channel_id))
    if ch is None:
        try:
            ch = await client_ref.fetch_channel(int(channel_id))
        except Exception as e:
            log.error("announcements: %s channel %s not found: %s", label, channel_id, e)
            return None
    return ch

# Discord's 2000-char limit is enforced server-side; the safest accounting is
# UTF-16 code units (what Discord's own clients count), and emoji cost 2 there.
# Field-level caps keep every template far below this; the clamp is a backstop
# that drops whole trailing lines instead of cutting a URL or mention in half.
MAX_POST_UNITS = 1900

def utf16_units(text):
    return len(text.encode("utf-16-le", errors="replace")) // 2

def clamp_post(text):
    if utf16_units(text) <= MAX_POST_UNITS:
        return text
    lines, total = [], 0
    for line in text.split("\n"):
        cost = utf16_units(line) + 1  # + the newline
        if total + cost > MAX_POST_UNITS:
            if not lines:
                # even the first line is oversized: hard-truncate it by
                # units, never splitting a surrogate pair mid-emoji
                kept, used = [], 0
                for ch in line:
                    cu = utf16_units(ch)
                    if used + cu > MAX_POST_UNITS - 4:
                        break
                    kept.append(ch)
                    used += cu
                return "".join(kept).rstrip() + " ..."
            break
        lines.append(line)
        total += cost
    return "\n".join(lines).rstrip() + "\n..."

async def _post(channel_id, text, label, image=None):
    if utf16_units(text) > MAX_POST_UNITS:
        log.warning("announcements: %s post was %d UTF-16 units, clamping", label, utf16_units(text))
        text = clamp_post(text)
    if DRY_RUN:
        log.info("announcements DRY-RUN would post to %s (%s):\n%s\nimage: %s",
                 channel_id, label, text, bool(image))
        return True
    ch = await _get_channel(channel_id, label)
    if ch is None:
        return False
    try:
        kwargs = {"allowed_mentions": _everyone_mentions}
        if image is not None:
            data, ext = image
            kwargs["file"] = discord.File(io.BytesIO(data), filename=f"event.{ext}")
        await ch.send(text, **kwargs)
        log.info("announcements: posted %s to %s", label, channel_id)
        return True
    except Exception as e:
        log.exception("announcements: failed posting %s: %s", label, e)
        return False

# --------------------------------------------------------------------------
# Morning loop
# --------------------------------------------------------------------------

_tzinfo = None
_tz_resolved = False

def _resolve_tz():
    global _tzinfo, _tz_resolved
    if _tz_resolved:
        return _tzinfo
    _tz_resolved = True
    if ZoneInfo is not None:
        try:
            _tzinfo = ZoneInfo(MORNING_TZ)
            return _tzinfo
        except Exception:
            pass
    fallback = _TZ_FALLBACK_OFFSETS.get(MORNING_TZ.strip().lower())
    if fallback is not None:
        log.warning("announcements: no tzdata for %r, using fixed UTC offset %s",
                    MORNING_TZ, fallback)
        _tzinfo = fallback
    else:
        log.warning("announcements: bad MORNING_TZ %r, using system local time", MORNING_TZ)
        _tzinfo = None
    return _tzinfo

def _now_local():
    tz = _resolve_tz()
    if tz is None:
        return datetime.datetime.now()
    return datetime.datetime.now(tz)

async def morning_loop():
    await client_ref.wait_until_ready()
    hh, mm = 9, 0
    try:
        hh, mm = (int(x) for x in MORNING_TIME.split(":")[:2])
    except Exception:
        log.warning("announcements: bad MORNING_TIME %r, using 09:00", MORNING_TIME)
    while True:
        try:
            now = _now_local()
            today = now.strftime("%Y-%m-%d")
            minutes = now.hour * 60 + now.minute
            start = hh * 60 + mm
            in_window = start <= minutes < start + MORNING_WINDOW_MINUTES
            if in_window and _state.get("morning_last") != today and not _state_broken:
                gifs = GIFS.get("good_morning") or []
                gif = random.choice(gifs) if gifs else None
                text = MORNING_TEXT + ("\n" + gif if gif else "")
                if await _post(MORNING_CHANNEL_ID, text, "morning"):
                    if not DRY_RUN:
                        _state["morning_last"] = today
                        save_state()
        except Exception:
            log.exception("announcements: morning loop error")
        await asyncio.sleep(30)

# --------------------------------------------------------------------------
# Codes + events loop
# --------------------------------------------------------------------------

def _parse_date_added(s, today):
    """game8 'M/D' -> date (this or last year, whichever is not future)."""
    m = re.match(r"(\d{1,2})/(\d{1,2})", s or "")
    if not m:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    for year in (today.year, today.year - 1):
        try:
            cand = datetime.date(year, mo, d)
        except ValueError:
            continue
        if cand <= today:
            return cand
    return None

async def seed_baseline():
    """One-time silent seed: record every code game8 lists, post nothing."""
    html = await fetch_text(GAME8_CODES_URL)
    if not html:
        log.error("announcements: baseline fetch failed; codes posting stays disabled")
        return False
    parsed = parse_game8(html)
    if not parsed:
        log.error("announcements: baseline parse failed (page layout changed?); codes posting stays disabled")
        return False
    active, expired = parsed
    if len(active) + len(expired) < SEED_MIN_CODES:
        log.error("announcements: baseline seed too small (%d codes); refusing to trust it",
                  len(active) + len(expired))
        return False
    _state["seen_codes"] = sorted({c["code"] for c in active})
    _state["expired_codes"] = sorted(set(expired))
    await seed_feeds()
    _state["seeded"] = True
    save_state()
    log.info("announcements: silent baseline seeded (%d active codes, %d expired, %d events, %d posts)",
             len(active), len(expired), len(_state["seen_events"]), len(_state["seen_posts"]))
    return True

async def seed_feeds():
    """Silently record everything currently in the announcement feeds as seen,
    so a bot that starts (or migrates) never backfills old posts. Idempotent."""
    if _state.get("feeds_seeded"):
        return
    seen_events = set(_state["seen_events"])
    seen_posts = set(_state["seen_posts"])
    for feed_type in (1, 2, 3):
        for item in await hoyo_list(feed_type):
            post = item.get("post", {})
            pid = str(post.get("post_id", ""))
            if not pid:
                continue
            if feed_type == 2:
                seen_events.add(pid)
            else:
                seen_posts.add(pid)
    _state["seen_events"] = sorted(seen_events)
    _state["seen_posts"] = sorted(seen_posts)
    _state["feeds_seeded"] = True
    save_state()
    log.info("announcements: feeds baseline recorded (%d events, %d posts), nothing posted",
             len(seen_events), len(seen_posts))

async def check_codes():
    if CODES_MODE == "off":
        return
    html = await fetch_text(GAME8_CODES_URL)
    if not html:
        return
    parsed = parse_game8(html)
    if not parsed:
        log.warning("announcements: game8 parse failed this cycle; skipping (state untouched)")
        return
    active, expired_now = parsed
    active_map = {c["code"]: c for c in active}

    seen = set(_state["seen_codes"])
    expired = set(_state["expired_codes"]) | set(expired_now)
    held = _state["held_codes"]

    # anything game8 now lists as expired can never be posted later
    for code in list(held):
        if code in expired_now or code not in active_map:
            del held[code]
            seen.add(code)
    _state["expired_codes"] = sorted(expired)

    today = _now_local().date()
    candidates = [c for c in active if c["code"] not in seen and c["code"] not in held]
    for c in candidates:
        held[c["code"]] = {"first_seen": today.isoformat(), "date_added": c["date_added"]}
        log.info("announcements: new code candidate held: %s", c["code"])

    if not held:
        _state["seen_codes"] = sorted(seen)
        save_state()
        return

    confirmed = []        # (item, via) ready to auto-post
    if CODES_MODE == "strict":
        bodies = await _official_post_bodies()
        for code in list(held):
            item = active_map.get(code)
            if not item:
                continue
            for body in bodies:
                if code in body and any(p in body.lower() for p in FRESH_PHRASES):
                    confirmed.append((item, "official"))
                    break
    elif CODES_MODE == "community":
        for code in list(held):
            item = active_map.get(code)
            if not item:
                continue
            if item.get("special"):
                # Program codes carry no Date Added. Auto-post only with BOTH a
                # parsed program version from the section heading AND
                # baseline-relative freshness (first seen right after the seed).
                # Anything ambiguous stays held, never auto-posts.
                first = datetime.date.fromisoformat(held[code]["first_seen"])
                if item.get("program") and (today - first).days <= COMMUNITY_FRESH_DAYS:
                    confirmed.append((item, "community"))
                continue
            added = _parse_date_added(item["date_added"], today)
            fresh = added is not None and (today - added).days <= COMMUNITY_FRESH_DAYS
            if fresh:
                confirmed.append((item, "community"))
            else:
                first = datetime.date.fromisoformat(held[code]["first_seen"])
                if (today - first).days > COMMUNITY_FRESH_DAYS + 3:
                    # never confirmed while fresh: stop tracking, never post late
                    del held[code]
                    seen.add(code)
                    log.info("announcements: held code %s went stale unconfirmed; skipped", code)
    else:
        log.warning("announcements: unknown ANNOUNCE_CODES_MODE %r; treating as off", CODES_MODE)

    if confirmed:
        specials = [it for it, _ in confirmed if it.get("special")]
        normals = [it for it, _ in confirmed if not it.get("special")]
        via = confirmed[0][1]
        posted_any = False
        if len(specials) >= 2:
            text = render_special_codes(specials)
            if via == "community":
                text += COMMUNITY_FOOTER
            if await _post(CODES_CHANNEL_ID, text, f"special codes x{len(specials)}"):
                posted_any = True
                seen.update(it["code"] for it in specials)
                for it in specials:
                    held.pop(it["code"], None)
        else:
            normals += specials  # a lone special-program code posts in normal format
        for it in normals:
            text = render_normal_code(it["code"], it.get("rewards"))
            if via == "community":
                text += COMMUNITY_FOOTER
            if await _post(CODES_CHANNEL_ID, text, f"code {it['code']}"):
                posted_any = True
                seen.add(it["code"])
                held.pop(it["code"], None)
        if posted_any:
            log.info("announcements: posted %d confirmed code(s) via %s", len(confirmed), via)

    _state["seen_codes"] = sorted(seen)
    save_state()

_official_body_cache = {}

async def _official_post_bodies():
    """Bodies of unseen official posts (types 1+3), marking them seen."""
    bodies = []
    for feed_type in (1, 3):
        for item in await hoyo_list(feed_type):
            post = item.get("post", {})
            pid = str(post.get("post_id", ""))
            if not pid or str(post.get("uid")) not in OFFICIAL_UIDS:
                continue
            if pid in _state["seen_posts"]:
                continue
            full = await hoyo_post(pid)
            _state["seen_posts"].append(pid)
            if not full:
                continue
            texts, _ = post_texts_and_images(full)
            bodies.append("\n".join(texts) + "\n" + re.sub(r"<[^>]+>", " ", full.get("content") or ""))
    # cap the seen_posts list so state does not grow forever (keep newest 500)
    _state["seen_posts"] = _state["seen_posts"][-500:]
    return bodies

async def check_events():
    for item in await hoyo_list(2):
        post = item.get("post", {})
        pid = str(post.get("post_id", ""))
        if not pid or pid in _state["seen_events"]:
            continue
        _state["seen_events"].append(pid)
        if str(post.get("uid")) not in OFFICIAL_UIDS:
            continue
        title = (post.get("subject") or "").strip()
        low = title.lower()
        if any(w in low for w in EVENT_EXCLUDE_WORDS):
            continue
        full = await hoyo_post(pid)
        if not full:
            continue
        texts, images = post_texts_and_images(full)
        duration, summary = event_details(texts)
        if "event" not in low and not duration:
            continue
        image = None
        for url in images:
            if IMG_HOST_ALLOWED not in url:
                continue
            data, ctype = await fetch_bytes(url, MAX_EVENT_IMAGE_BYTES)
            if data and ctype.startswith("image/") and len(data) >= MIN_EVENT_IMAGE_BYTES:
                ext = "png" if "png" in ctype else ("webp" if "webp" in ctype else "jpg")
                image = (data, ext)
                break
        text = render_event(title, summary, duration, pid)
        await _post(EVENTS_CHANNEL_ID, text, f"event {pid}", image=image)
    _state["seen_events"] = _state["seen_events"][-500:]
    save_state()

async def watch_loop():
    await client_ref.wait_until_ready()
    await asyncio.sleep(20)  # let the bot settle after login
    if _state_broken:
        return
    if not _state.get("seeded"):
        await seed_baseline()   # silent: records current codes, posts nothing
    else:
        await seed_feeds()      # migration for pre-feeds-seed states: silent, no-op when already done
    while True:
        try:
            await check_codes()
            await check_events()
        except Exception:
            log.exception("announcements: watch loop error")
        await asyncio.sleep(CHECK_MINUTES * 60)


# --------------------------------------------------------------------------
# New-member welcome (event-driven, needs the Server Members intent)
# --------------------------------------------------------------------------

# Exact wording approved by Fazal (2026-10-31). Server name and member
# mention are filled in automatically; only the joining member is pinged.
WELCOME_TEXT = "Welcome to **{server}**, {mention}! Make yourself at home, we're glad you're here 💙"

def _member_mentions(member):
    """Ping ONLY the joining member - never everyone or roles."""
    return discord.AllowedMentions(users=[member], everyone=False, roles=False)

async def _on_member_join(member):
    if member.bot:
        return  # don't welcome other bots
    gifs = GIFS.get("hello") or []
    gif = random.choice(gifs) if gifs else None
    text = WELCOME_TEXT.format(server=member.guild.name, mention=member.mention)
    if gif:
        text += "\n" + gif
    if DRY_RUN:
        log.info("announcements DRY-RUN would welcome %s in %s:\n%s", member, member.guild.name, text)
        return
    ch = await _get_channel(WELCOME_CHANNEL_ID, "welcome")
    if ch is None:
        return
    try:
        await ch.send(text, allowed_mentions=_member_mentions(member))
        log.info("announcements: welcomed %s in %s", member, member.guild.name)
    except Exception as e:
        log.exception("announcements: welcome failed: %s", e)

# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

client_ref = None
_tasks = []

_started = False

def start(client):
    global client_ref, _started
    if _started:
        return  # on_ready can fire again after reconnects; never double-start
    _started = True
    client_ref = client
    load_state()
    if not ANNOUNCE_ENABLED:
        log.info("announcements: disabled via ANNOUNCE_ENABLED=0")
        return
    if DRY_RUN:
        log.info("announcements: DRY RUN - nothing will actually be posted")
    _tasks.append(asyncio.create_task(morning_loop()))
    _tasks.append(asyncio.create_task(watch_loop()))
    if WELCOME_ENABLED:
        # on_member_join needs intents.members=True in bot.py AND the
        # "Server Members Intent" toggle ON in the Discord developer portal.
        client.event(_on_member_join)
        log.info("announcements: welcome listener registered -> %s", WELCOME_CHANNEL_ID)
    log.info("announcements: started (morning %s %s -> %s, codes mode=%s -> %s, events -> %s)",
             MORNING_TIME, MORNING_TZ, MORNING_CHANNEL_ID, CODES_MODE, CODES_CHANNEL_ID, EVENTS_CHANNEL_ID)

# --------------------------------------------------------------------------
# Standalone self-test: python announcements.py --selftest
# Fetches live sources and prints what it finds WITHOUT a Discord connection.
# --------------------------------------------------------------------------

async def _selftest():
    print("== game8 fetch/parse ==")
    html = await fetch_text(GAME8_CODES_URL)
    if not html:
        print("FAIL: game8 fetch")
        return
    parsed = parse_game8(html)
    if not parsed:
        print("FAIL: parse (layout changed?)")
        return
    active, expired = parsed
    print(f"active codes: {len(active)}, expired codes: {len(expired)}")
    for c in active:
        print(f"  {c['code']:<18} added={c['date_added'] or '?':<7} special={c['special']} rewards={c['rewards']}")
    print("\n== sample renders (no posting) ==")
    if active:
        print(render_normal_code(active[0]["code"], active[0]["rewards"]))
        print()
        print(render_special_codes(active[:3]))
    print("\n== events feed check ==")
    for item in await hoyo_list(2):
        post = item.get("post", {})
        pid = str(post.get("post_id", ""))
        if str(post.get("uid")) not in OFFICIAL_UIDS:
            continue
        title = (post.get("subject") or "").strip()
        low = title.lower()
        verdict = "SKIP(excluded)" if any(w in low for w in EVENT_EXCLUDE_WORDS) else ("ok" if "event" in low else "skip(no event kw)")
        print(f"  {pid} {verdict:<16} {title[:60]}")
    print("\n== event detail sample (first 'ok' post) ==")
    for item in await hoyo_list(2):
        post = item.get("post", {})
        if str(post.get("uid")) not in OFFICIAL_UIDS:
            continue
        title = (post.get("subject") or "").strip().lower()
        if "event" not in title or any(w in title for w in EVENT_EXCLUDE_WORDS):
            continue
        full = await hoyo_post(str(post["post_id"]))
        texts, images = post_texts_and_images(full)
        duration, summary = event_details(texts)
        print(render_event(post.get("subject"), summary, duration, post["post_id"]))
        pub = [u for u in images if IMG_HOST_ALLOWED in u]
        print("image:", pub[0] if pub else "(none on public host)")
        break
    if _session:
        await _session.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if os.environ.get("MEMORY_DIR") is None:
        MEMORY_DIR = "./memory"
    asyncio.run(_selftest())
