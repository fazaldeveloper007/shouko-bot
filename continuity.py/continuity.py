"""Conversation-continuation detection for direct 1:1 exchanges.

When Kristy ends her reply to a member with a question, that member's next
message in the same channel is plausibly the answer, even without a
name-drop, @mention or native reply. This module tracks that short window
per (channel, member) with hard fail-silent rules:

- A record is created ONLY when her reply ends with a question, and expires
  after CONTINUITY_MINUTES (default 4).
- Any message that triggers her directly (name, mention, reply-to-her,
  command follow-up) invalidates everyone else's records in that channel:
  the conversation spotlight moved.
- Any human message that is NOT accepted as a continuation invalidates all
  records in the channel: interleaved chat is ambiguity, and ambiguity
  means silence.
- A candidate is rejected when it @mentions someone, natively replies to a
  message that is not hers, or arrives after expiry.
- An accepted continuation is consumed: the next one needs a fresh
  question from her, so a record can never self-sustain.

This changes only when she replies to a message from the member she was
just talking to. It never makes her speak first and never widens the
proactive channel scope. CONTINUITY_ENABLED=0 turns the feature off.
"""

import logging
import os
import re
import time

log = logging.getLogger("kristy.continuity")


def _flag(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


ENABLED = _flag("CONTINUITY_ENABLED", "1")
WINDOW_MINUTES = float(os.getenv("CONTINUITY_MINUTES", "4"))

# "Did she ask something?" - a question mark in the tail of her reply,
# allowing trailing emoji/punctuation after it.
_QUESTION_TAIL_RE = re.compile(r"[?？][^?？]{0,40}$")


class Continuity:
    """Per-(channel, member) pending-answer windows. In-memory only."""

    def __init__(self, clock=time.monotonic, window_minutes=None, enabled=None):
        self._clock = clock
        self._window = (window_minutes if window_minutes is not None
                        else WINDOW_MINUTES) * 60.0
        self._enabled = ENABLED if enabled is None else enabled
        self._expires = {}  # (channel_id, user_id) -> monotonic deadline

    def _clear_channel(self, channel_id, keep_user=None):
        for cid, uid in [k for k in self._expires if k[0] == channel_id]:
            if keep_user is not None and uid == keep_user:
                continue
            del self._expires[(cid, uid)]

    def note_bot_reply(self, channel_id, user_id, reply_text):
        """Her conversational reply went out. Arm a window for that member
        only when she actually asked something; either way, everyone else's
        pending windows in the channel are over."""
        if not self._enabled:
            return
        self._clear_channel(channel_id)
        if reply_text and _QUESTION_TAIL_RE.search(reply_text.rstrip()):
            self._expires[(channel_id, user_id)] = self._clock() + self._window

    def note_direct_trigger(self, channel_id, user_id):
        """A message triggered her directly. Any prior window for other
        members is over; this member's window is superseded by the reply
        that is about to happen."""
        if not self._enabled:
            return
        self._clear_channel(channel_id)

    def note_interruption(self, channel_id):
        """A human message neither triggered her nor was accepted as a
        continuation: interleaved chat, so every pending window here is
        ambiguous - drop them all (fail silent)."""
        if not self._enabled:
            return
        self._clear_channel(channel_id)

    def is_continuation(self, channel_id, user_id, mentions_other=False,
                        has_reference=False):
        """True, once, when this message is the pending answer. Explicit
        signals that it is for someone else (a mention, a native reply)
        always win over the window."""
        if not self._enabled or mentions_other or has_reference:
            return False
        deadline = self._expires.get((channel_id, user_id))
        if deadline is None:
            return False
        if self._clock() > deadline:
            del self._expires[(channel_id, user_id)]
            return False
        del self._expires[(channel_id, user_id)]  # consumed, never self-sustaining
        return True
