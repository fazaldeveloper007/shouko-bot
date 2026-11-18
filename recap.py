"""Kristy's long-term channel memory: a rolling AI recap per channel.

Her live history only keeps the last HISTORY_LEN messages. Instead of
forgetting everything older, each line that falls out of the history deque
is buffered here; every RECAP_EVERY overflowed lines the buffer is folded
into a short running recap by one small AI call. The recap is prepended to
the brain's context, so she remembers what happened before the recent
messages without blowing up the prompt or the free-tier quota.

Everything is fail-open: a missing file, a write error or a failed AI call
never breaks replying. Recaps are keyed by channel ID, so servers never see
each other's memory. The recap is chat data, never instructions - the
summarizer prompt says so, and the brain is told so too.
"""

import json
import os
import time
from pathlib import Path

RECAP_EVERY = 12       # overflowed lines between recap updates
RECAP_MAX_CHARS = 2400 # hard cap on the stored recap: bounded, but room for
                       # attributed facts instead of a thin gist
PENDING_CAP = 30       # buffered lines kept if updates keep failing
CHANNEL_CAP = 200      # stored channel recaps; least-recently-active evicted
AUTHOR_CHARS = 40
LINE_CHARS = 300

PATH = Path(os.getenv("MEMORY_DIR") or Path(__file__).resolve().parent / "memory") / "channel-recaps.json"

_cache = None   # lazy-loaded {channel_id: {"recap": str, "pending": [[author, line], ...]}}
_running = set()  # channel IDs with an update in flight


def _load():
    global _cache
    if _cache is None:
        try:
            data = json.loads(PATH.read_text(encoding="utf-8"))
            _cache = data if isinstance(data, dict) else {}
        except Exception:
            _cache = {}
    return _cache


def _save():
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_load(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(PATH)
    except Exception:
        pass  # memory is best-effort; never break chat over a disk error


def get(channel_id):
    """The channel's current recap ("" when there is none)."""
    state = _load().get(str(channel_id)) or {}
    text = state.get("recap")
    return text if isinstance(text, str) else ""


def _evict():
    """Bound disk/memory: keep only the CHANNEL_CAP most recently active channels."""
    cache = _load()
    if len(cache) <= CHANNEL_CAP:
        return
    def activity(item):
        state = item[1]
        return state.get("updated", 0) if isinstance(state, dict) else 0
    for cid, _state in sorted(cache.items(), key=activity)[: len(cache) - CHANNEL_CAP]:
        cache.pop(cid, None)


def note(channel_id, author, line):
    """Buffer one line that fell out of live history.

    Returns True exactly when a recap update should be scheduled now.
    """
    cid = str(channel_id)
    cache = _load()
    state = cache.setdefault(cid, {"recap": "", "pending": []})
    state["updated"] = time.time()
    pending = state.setdefault("pending", [])
    pending.append([str(author)[:AUTHOR_CHARS], str(line)[:LINE_CHARS]])
    del pending[:-PENDING_CAP]
    _evict()
    due = len(pending) >= RECAP_EVERY and cid not in _running
    if due or len(pending) % 5 == 0:
        _save()
    return due


async def update(channel_id, summarize):
    """Fold the buffered lines into the recap via one summarize() call.

    summarize(old_recap, lines) must return the new recap text and raise on
    failure; the buffered lines are then kept for the next attempt.
    """
    cid = str(channel_id)
    if cid in _running:
        return
    _running.add(cid)
    try:
        state = _load().setdefault(cid, {"recap": "", "pending": []})
        state["updated"] = time.time()
        lines = state.get("pending") or []
        if not lines:
            return
        state["pending"] = []
        try:
            new_recap = await summarize(state.get("recap", ""), lines)
        except Exception:
            state["pending"] = (lines + state["pending"])[-PENDING_CAP:]
            _save()
            return
        new_recap = (new_recap or "").strip()
        if new_recap:  # an empty summary must not wipe the old recap
            state["recap"] = new_recap[:RECAP_MAX_CHARS]
        _save()
    finally:
        _running.discard(cid)


def reset(channel_id):
    """Drop one channel's recap (used by tests; no chat command calls this)."""
    data = _load()
    if str(channel_id) in data:
        del data[str(channel_id)]
        _save()
