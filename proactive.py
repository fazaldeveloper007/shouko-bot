"""Kristy's proactive unanswered-help, scoped to Gaming Impact's text channels.

OFF BY DEFAULT (PROACTIVE_ENABLED=1 to allow). When someone in an allowed
channel asks a question or asks for a review/opinion and nobody answers,
Kristy may - once - add her own input, as herself, never impersonating the
person the question was addressed to.

Hard rules baked in here, not in the prompt:
- No intentional wait before she answers a tracked question; the AI
  verdict, per-channel daily cap and cooldown, quiet hours and the
  answer-recheck still gate every post.
- A pending question is cancelled when anyone reply-references it, when the
  asker posts a follow-up (they moved on), or when the AI verdict says the
  chat already answered it. Unrelated chatter from others cancels NOTHING.
- Per-channel daily cap and cooldown, quiet hours, no mentions by default.
- She never replies to bots, to herself, or uses diary/private memory -
  the decider sees public channel history only.
- Channel resolution is fail-closed: the guild is derived from the existing
  configured chat channel, Gaming-Chat/Clips-Highlights come from existing
  config, Truth-Dare must match exactly one text channel by name in that
  guild; anything missing or duplicated disables the feature with a log
  line naming why, instead of posting anywhere unintended.
"""

import datetime
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("kristy.proactive")


def _flag(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


ENABLED = _flag("PROACTIVE_ENABLED", "0")
MENTIONS = _flag("PROACTIVE_MENTIONS", "0")  # default: no tags on proactive posts
GRACE_MINUTES = int(os.getenv("PROACTIVE_GRACE_MINUTES", "0"))  # no intentional wait; verdict + caps still gate
OPENER_IDLE_HOURS = float(os.getenv("PROACTIVE_OPENER_IDLE_HOURS", "4"))
COOLDOWN_MINUTES = int(os.getenv("PROACTIVE_COOLDOWN_MINUTES", "60"))
DAILY_CAP = int(os.getenv("PROACTIVE_DAILY_CAP", "3"))
QUIET_START = os.getenv("PROACTIVE_QUIET_START", "23:00")  # IST, inclusive
QUIET_END = os.getenv("PROACTIVE_QUIET_END", "08:00")      # IST, exclusive
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

MEMORY_DIR = os.getenv("MEMORY_DIR", "./memory")
STATE_PATH = os.path.join(MEMORY_DIR, "proactive-state.json")

# Cheap prefilter only: does this message LOOK like a help bid worth tracking?
# The actual "should she answer" decision is an AI call at expiry time.
_QUESTION_RE = re.compile(
    r"[?？]|"
    r"\b(review|opinion|kaisa laga|kya lagta|kya lagti|check karo|check this|"
    r"batao|btao|koi hai|anyone|help|suggest|kaun sa|kaun si|kaise|kyun|kya karu|"
    r"kaisi hai|kya hai)\b",
    re.IGNORECASE)


def looks_like_help_bid(content):
    text = (content or "").strip()
    return len(text) >= 8 and bool(_QUESTION_RE.search(text))


def _norm_channel_name(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


class Proactive:
    """Tracker + poster. All sends go through post_fn(channel, text) so tests
    never touch Discord; the decider is decide_fn(history, question) -> dict."""

    def __init__(self, state=None, now=None):
        self.state = state if state is not None else self._load()
        self._now = now or time.time

    # ---- state ----

    def _default(self):
        return {"pending": {}, "daily": {}, "last_post": {}, "last_human": {}}

    def _load(self):
        try:
            data = json.loads(Path(STATE_PATH).read_text(encoding="utf-8"))
            return {**self._default(), **data}
        except Exception:
            return self._default()

    def save(self):
        try:
            Path(STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
            Path(STATE_PATH).write_text(json.dumps(self.state, ensure_ascii=False),
                                        encoding="utf-8")
        except Exception:
            pass  # best-effort; never break chat over bookkeeping

    # ---- tracking ----

    def track(self, channel_id, message_id, author_id, content):
        """Record a possible help bid. One pending per author per channel."""
        if not looks_like_help_bid(content):
            return False
        key = f"{channel_id}:{author_id}"
        self.state["pending"][key] = {
            "channel_id": channel_id, "message_id": message_id,
            "author_id": author_id, "content": (content or "")[:400],
            "ts": self._now()}
        self.save()
        return True

    def cancel_if_addressed(self, channel_id, author_id, reply_to_id):
        """A new human message arrived: cancel pendings it actually addresses.

        - a reply-reference to the pending message: answered, cancel;
        - the asker's own follow-up: they moved on, cancel;
        - anyone else's unrelated chatter: cancel NOTHING.
        """
        for key, p in list(self.state["pending"].items()):
            if p["channel_id"] != channel_id:
                continue
            if reply_to_id and p["message_id"] == reply_to_id:
                del self.state["pending"][key]
                log.info("proactive: pending %s answered by reply", key)
            elif author_id == p["author_id"]:
                del self.state["pending"][key]
                log.info("proactive: pending %s cancelled (asker follow-up)", key)
        self.save()

    def cancel_addressed_by_reply(self, channel_id, author_id, message_id):
        """Kristy directly replied to this member in this channel: any pending
        help bid of theirs here is addressed - cancel it so she never
        double-answers at the loop tick."""
        for key, p in list(self.state["pending"].items()):
            if p["channel_id"] != channel_id or p["author_id"] != author_id:
                continue
            del self.state["pending"][key]
            log.info("proactive: pending %s addressed by her direct reply", key)
        self.save()

    # ---- gating ----

    def note_activity(self, channel_id):
        """Every human message in an allowed channel resets the opener idle clock."""
        self.state["last_human"][str(channel_id)] = self._now()
        self.save()

    def seed_activity(self, channel_id):
        """Startup: no history known, so idle clock starts at boot (no instant opener)."""
        self.state["last_human"].setdefault(str(channel_id), self._now())

    def opener_due(self, channel_id):
        """True when the channel has been human-quiet long enough for one opener."""
        last = self.state["last_human"].get(str(channel_id), self._now())
        return self._now() - last >= OPENER_IDLE_HOURS * 3600

    def due(self, channel_id):
        """Pending items in this channel past the grace window."""
        now = self._now()
        return [p for p in self.state["pending"].values()
                if p["channel_id"] == channel_id
                and now - p["ts"] >= GRACE_MINUTES * 60]

    def quiet_now(self, now_dt=None):
        now_dt = now_dt or datetime.datetime.now(IST)
        hhmm = now_dt.strftime("%H:%M")
        if QUIET_START <= QUIET_END:
            return QUIET_START <= hhmm < QUIET_END
        return hhmm >= QUIET_START or hhmm < QUIET_END  # overnight window

    def allowed_today(self, channel_id):
        today = datetime.datetime.now(IST).strftime("%Y-%m-%d")
        daily = self.state["daily"].get(f"{channel_id}:{today}", 0)
        last = self.state["last_post"].get(str(channel_id), 0)
        return (daily < DAILY_CAP
                and self._now() - last >= COOLDOWN_MINUTES * 60)

    def mark_posted(self, channel_id, pending_key):
        today = datetime.datetime.now(IST).strftime("%Y-%m-%d")
        dkey = f"{channel_id}:{today}"
        self.state["daily"][dkey] = self.state["daily"].get(dkey, 0) + 1
        self.state["last_post"][str(channel_id)] = self._now()
        self.state["pending"].pop(pending_key, None)
        # bound state: drop daily counters older than today
        for k in list(self.state["daily"]):
            if not k.endswith(today):
                del self.state["daily"][k]
        self.save()

    def drop(self, pending_key):
        self.state["pending"].pop(pending_key, None)
        self.save()

    # ---- channel resolution (fail-closed) ----

    @staticmethod
    def resolve_channels(guild, gaming_chat_id, clips_id):
        """The 3 allowed text channels, or None with a logged reason.

        Guild comes from the established configured channels themselves;
        Truth-Dare must match exactly one text channel by name in it.
        """
        if guild is None:
            log.error("proactive: no guild resolved from configured channels; disabled")
            return None
        channels = {}
        for label, cid in (("gaming-chat", gaming_chat_id), ("clips-highlights", clips_id)):
            ch = guild.get_channel(int(cid)) if cid else None
            if ch is None:
                log.error("proactive: configured %s channel %s not in guild %s; disabled",
                          label, cid, getattr(guild, "id", "?"))
                return None
            channels[label] = ch
        want = _norm_channel_name("truth-dare")
        matches = [c for c in getattr(guild, "text_channels", [])
                   if _norm_channel_name(getattr(c, "name", "")) == want]
        if len(matches) != 1:
            log.error("proactive: expected exactly 1 truth-dare channel in guild %s, "
                      "found %d; disabled", getattr(guild, "id", "?"), len(matches))
            return None
        channels["truth-dare"] = matches[0]
        log.info("proactive: scoped to guild %s channels %s",
                 guild.id, {k: c.id for k, c in channels.items()})
        return channels
