"""
Server-scoped nickname registry for Kristy ("X bole toh is member ko tag karo").

Separate from the diary on purpose: diaries are per-member, AI-reworded,
capped short-term notes about ONE person; aliases are a server-level
identity mapping (a name people type -> a verified Discord member ID),
shared by everyone in that server and written only from explicit teaching
messages. One JSON file, aliases.json, inside MEMORY_DIR (gitignored, stays
on the host phone). Same fail-open rules as diary.py: a missing or corrupt
file is logged and treated as empty, it must never break replying.

Safety properties enforced HERE (callers add the guild-context checks):
- an alias that is already taken is never silently retargeted; it must be
  forgotten first, then re-taught
- aliases are short plain text: no mention syntax, no control chars
- everything is keyed per guild, so names never leak across servers
"""

import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("kristy.aliases")

_ALIASES_FILE = Path(
    os.getenv("ALIASES_FILE")
    or (Path(os.getenv("MEMORY_DIR") or (Path(__file__).resolve().parent / "memory")) / "aliases.json")
)
MAX_ALIASES_PER_GUILD = 100
MAX_ALIAS_CHARS = 32
MIN_ALIAS_CHARS = 2

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_STRIP_RE = re.compile(r"[<>`@#*_~|\\\"']+")


def clean_alias(text):
    """One safe, short, single-line alias (or '' if unusable)."""
    if not isinstance(text, str):
        return ""
    text = _CONTROL_RE.sub(" ", text)
    text = _STRIP_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -.")
    if not (MIN_ALIAS_CHARS <= len(text) <= MAX_ALIAS_CHARS):
        return ""
    if not re.search(r"[\w]", text, re.UNICODE):
        return ""
    return text


def _key(alias):
    return alias.casefold()


def _empty():
    return {"v": 1, "guilds": {}}


def _load_all():
    """Read the whole registry. Missing/corrupt -> empty (never raises)."""
    try:
        with open(_ALIASES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("guilds"), dict):
            raise ValueError("aliases file is not a registry object")
        guilds = {}
        for gid, entries in data["guilds"].items():
            if not isinstance(entries, dict):
                continue
            clean = {}
            for k, rec in entries.items():
                if not isinstance(rec, dict):
                    continue
                uid = rec.get("id")
                label = clean_alias(rec.get("label") or k)
                if label and isinstance(uid, int):
                    clean[_key(label)] = {
                        "id": uid,
                        "label": label,
                        "by": rec.get("by") if isinstance(rec.get("by"), int) else 0,
                        "ts": rec.get("ts") if isinstance(rec.get("ts"), (int, float)) else 0,
                    }
            if clean:
                guilds[str(gid)] = clean
        return {"v": 1, "guilds": guilds}
    except FileNotFoundError:
        return _empty()
    except Exception as e:
        log.warning("Aliases file unreadable, starting fresh: %s", e)
        return _empty()


def _save_all(data):
    """Atomic write. Returns True on success, never raises."""
    try:
        _ALIASES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ALIASES_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _ALIASES_FILE)
        return True
    except Exception as e:
        log.warning("Could not save aliases file: %s", e)
        return False


def set_alias(guild_id, alias, user_id, by_id=0, now=None):
    """Teach alias -> member for one guild.

    Returns (status, existing_user_id):
      'saved'   alias stored (existing_user_id is None)
      'taken'   alias already maps to a DIFFERENT member; not changed
                (existing_user_id is the current owner's id)
      'invalid' alias text unusable
      'full'    guild hit the alias cap
    Re-teaching the same alias for the SAME member just refreshes it.
    """
    label = clean_alias(alias)
    if not label or not isinstance(user_id, int):
        return "invalid", None
    data = _load_all()
    guild = data["guilds"].setdefault(str(guild_id), {})
    k = _key(label)
    existing = guild.get(k)
    if existing and existing["id"] != user_id:
        return "taken", existing["id"]
    if existing is None and len(guild) >= MAX_ALIASES_PER_GUILD:
        return "full", None
    guild[k] = {
        "id": user_id,
        "label": label,
        "by": by_id if isinstance(by_id, int) else 0,
        "ts": now if now is not None else int(time.time()),
    }
    if not _save_all(data):
        return "error", None
    return "saved", None


def remove_alias(guild_id, alias):
    """Forget one alias for one guild. Returns True if something was removed."""
    k = _key(clean_alias(alias) or alias or "")
    data = _load_all()
    guild = data["guilds"].get(str(guild_id)) or {}
    if k not in guild:
        return False
    del guild[k]
    if not guild:
        data["guilds"].pop(str(guild_id), None)
    return _save_all(data)


def resolve(guild_id, alias):
    """alias -> member user_id for this guild, or None."""
    k = _key(clean_alias(alias) or alias or "")
    rec = (_load_all()["guilds"].get(str(guild_id)) or {}).get(k)
    return rec["id"] if rec else None


def list_aliases(guild_id):
    """All (label, user_id) pairs for one guild, for owner introspection."""
    guild = _load_all()["guilds"].get(str(guild_id)) or {}
    return [(rec["label"], rec["id"]) for rec in guild.values()]
