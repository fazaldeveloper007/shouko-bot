"""Kristy's proactive unanswered-help, scoped to Gaming Impact's text channels.

OFF BY DEFAULT (PROACTIVE_ENABLED=1 to allow). When someone in an allowed
channel asks a question or asks for a review/opinion and nobody answers,
Kristy may - once - add her own input, as herself, never impersonating the
person the question was addressed to.

Hard rules baked in here, not in the prompt:
- People get a grace window first; she never jumps in instantly.
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
GRACE_MINUTES = int(os.getenv("PROACTIVE_GRACE_MINUTES", "20"))
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
        return {"pending": {}, "daily": {}, "last_post": {}}

    def _load(self):
        try:
            data = json.loads(Path(STATE_PATH).read_text(encoding="utf-8"))
            return {**self._default(), **data}
        except Exception:
            return self._default()

    def save(self):
