"""Shared persistent action core.

One durable ledger (SQLite) records conversational pending states and
action outcomes for reminders, announcements and server plans, so a
restart loses neither a half-finished confirmation nor the verified
result of an executed action. Chat and the scheduled tools record
through the same store.

Execution-truth rule: a task is marked done only after its effect is
verified (store read-back or Discord accept); anything else stays
failed/cancelled with its reason - never a fake "done".
"""
import datetime as dt
import json
import os
import secrets
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  guild TEXT, channel TEXT, author TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  due REAL, expires REAL,
  result TEXT, last_error TEXT,
  created REAL NOT NULL,
  updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_tasks_pending ON tasks(status, kind, expires);
"""

PENDING = 'pending_confirm'
DONE = 'done'
FAILED = 'failed'
CANCELLED = 'cancelled'
EXPIRED = 'expired'
FINAL_STATUSES = (DONE, FAILED, CANCELLED, EXPIRED)


class TaskLedger:
    """Durable pending/action ledger, backed by one SQLite file."""

    def __init__(self, path):
        self.path = str(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)

    def close(self):
        try:
            self.db.close()
        except Exception:
            pass

    def create(self, kind, status, *, guild=None, channel=None, author=None,
               payload=None, due=None, expires=None, result=None, last_error=None, ident=None):
        now = time.time()
        ident = ident or secrets.token_hex(8)
        self.db.execute(
            'INSERT OR REPLACE INTO tasks(id,kind,status,guild,channel,author,payload,due,expires,result,last_error,created,updated)'
            ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident, kind, status, str(guild or ''), str(channel or ''), str(author or ''),
             json.dumps(payload or {}, ensure_ascii=False), due, expires, result, last_error, now, now))
        self.db.commit()
        return ident

    def get(self, ident):
        cur = self.db.execute('SELECT * FROM tasks WHERE id=?', (ident,))
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(zip([c[0] for c in cur.description], row))
        d['payload_raw'] = d['payload']
        try:
            d['payload'] = json.loads(d['payload_raw'] or '{}')
        except (ValueError, TypeError):
            d['payload'] = {}
        return d

    def update(self, ident, *, status=None, payload=None, result=None, last_error=None, expires=None):
        row = self.get(ident)
        if row is None:
            return False
        self.db.execute(
            'UPDATE tasks SET status=?,payload=?,result=COALESCE(?,result),'
            'last_error=COALESCE(?,last_error),expires=COALESCE(?,expires),updated=? WHERE id=?',
            (status or row['status'],
             json.dumps(payload, ensure_ascii=False) if payload is not None else row['payload_raw'],
             result, last_error, expires, time.time(), ident))
        self.db.commit()
        return True

    def pending(self, kind=None, now=None):
        """Unexpired pending_confirm rows, oldest first."""
        now = time.time() if now is None else now
        if kind is None:
            cur = self.db.execute(
                "SELECT id FROM tasks WHERE status=? AND (expires IS NULL OR expires>?) ORDER BY created",
                (PENDING, now))
        else:
            cur = self.db.execute(
                "SELECT id FROM tasks WHERE status=? AND kind=? AND (expires IS NULL OR expires>?) ORDER BY created",
                (PENDING, kind, now))
        return [r for r in (self.get(i) for (i,) in cur.fetchall()) if r is not None]

    def purge_expired(self, now=None):
        now = time.time() if now is None else now
        cur = self.db.execute(
            "UPDATE tasks SET status=?,updated=? WHERE status=? AND expires IS NOT NULL AND expires<=?",
            (EXPIRED, now, PENDING, now))
        self.db.commit()
        return cur.rowcount

    def settle_key(self, kinds, guild, channel, author, status):
        """Mark every pending row of the given kinds for one (guild, channel,
        author) conversation slot with a final status. Returns rows settled."""
        now = time.time()
        marks = ','.join('?' * len(kinds))
        cur = self.db.execute(
            f"UPDATE tasks SET status=?,updated=? WHERE status=? AND kind IN ({marks})"
            ' AND guild=? AND channel=? AND author=?',
            (status, now, PENDING, *kinds, str(guild), str(channel), str(author)))
        self.db.commit()
        return cur.rowcount


# ---- pending-item (de)serialization -------------------------------------
# Commands pending items are tuples: (monotonic_expiry, message_id, request,
# anchor_datetime[, marker, extra]). Datetimes are stored as timestamps;
# the monotonic expiry is rebuilt from the wall-clock expires on restore.

def pack_pending_item(item):
    out = []
    for v in item:
        if isinstance(v, dt.datetime):
            out.append({'__ts__': v.timestamp()})
        else:
            out.append(v)
    return {'item': out}


def unpack_pending_item(payload, expires_wall, now=None):
    item = []
    for v in (payload or {}).get('item', []):
        if isinstance(v, dict) and '__ts__' in v:
            item.append(dt.datetime.fromtimestamp(v['__ts__'], dt.timezone.utc))
        else:
            item.append(v)
    item[0] = time.monotonic() + max(0.0, expires_wall - (time.time() if now is None else now))
    return tuple(item)


def wall_from_monotonic(mono_expiry):
    return time.time() + max(0.0, mono_expiry - time.monotonic())


def verify_saved(store, ident):
    """Read-back verification: the scheduled job exists and is still armed.
    A create that cannot be read back is treated as not saved."""
    try:
        data = store.load()
    except Exception:
        return None
    return next((e for e in data if e.get('id') == ident), None)
