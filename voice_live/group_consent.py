"""Private persistent, versioned future-session grants from real interactions.

No provider/persona/diary imports. Scope binds guild, bot, fixed allowed audience
and processing notice revision. Channel changes alone do not broaden audience.
POSIX lock and atomic replace support Termux. Grant data never enters git.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from voice_live.group_policy import GroupPolicyError


def scope_key(scope):
    value = dict(guild=scope.guild, bot=scope.bot,
        audience=sorted(scope.allowlist), revision=scope.notice_revision,
        channel_gate=scope.channel_gate,
        purpose='adult-nonsensitive-native-live-no-diary-future-sessions',
        model='gemini-3.1-flash-live-preview', voice='Sulafat',
        handling='Google-Free-product-improvement-human-review',
        diary=False, recording=False, transcript_files=False)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ConsentStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextlib.contextmanager
    def _lock(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'a') as lock:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _read(self):
        try:
            if self.path.stat().st_size > 65536:
                raise GroupPolicyError('GROUP_CONSENT_STORE_INVALID')
            value = json.loads(self.path.read_text())
        except FileNotFoundError:
            return dict(schema=1, current_scope=None, grants={})
        except (ValueError, OSError):
            raise GroupPolicyError('GROUP_CONSENT_STORE_INVALID') from None
        if (not isinstance(value, dict) or value.get('schema') != 1 or
                not isinstance(value.get('grants'), dict) or len(value['grants']) > 1024 or
                (value.get('current_scope') is not None and
                 (not isinstance(value.get('current_scope'), str) or len(value['current_scope']) != 64))):
            raise GroupPolicyError('GROUP_CONSENT_STORE_INVALID')
        for actor, grant in value['grants'].items():
            if (not isinstance(actor, str) or not actor.isascii() or not actor.isdigit()
                    or not 15 <= len(actor) <= 22 or not isinstance(grant, dict)
                    or set(grant) != {'scope', 'future', 'adult', 'processing'}
                    or not isinstance(grant['scope'], str) or len(grant['scope']) != 64
                    or any(grant[k] is not True for k in ('future', 'adult', 'processing'))):
                raise GroupPolicyError('GROUP_CONSENT_STORE_INVALID')
        return value

    def _write(self, value):
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent,
                    prefix='.live-consent-', delete=False) as f:
                name = f.name
                os.fchmod(f.fileno(), 0o600)
                json.dump(value, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
            name = None
        except OSError:
            raise GroupPolicyError('GROUP_CONSENT_SAVE_FAILED') from None
        finally:
            if name:
                Path(name).unlink(missing_ok=True)

    def valid(self, scope):
        with self._lock():
            value = self._read()
            expected = scope_key(scope)
            if value.get('current_scope') != expected:
                value = dict(schema=1, current_scope=expected, grants={})
                self._write(value)
            return frozenset(actor for actor, grant in value['grants'].items()
                if actor in scope.allowlist and grant['scope'] == expected)

    def agree(self, scope, actor, *, adult, processing, future):
        # Caller must be the authenticated Discord interaction adapter, never
        # owner-provided assertions about other members or model output.
        if actor not in scope.allowlist or any(v is not True for v in (adult, processing, future)):
            raise GroupPolicyError('GROUP_AGREEMENT_INVALID')
        with self._lock():
            value = self._read()
            expected = scope_key(scope)
            if value.get('current_scope') != expected:
                value = dict(schema=1, current_scope=expected, grants={})
            value['grants'][actor] = dict(scope=expected,
                adult=True, processing=True, future=True)
            self._write(value)
            if self._read()['grants'].get(actor) != value['grants'][actor]:
                raise GroupPolicyError('GROUP_CONSENT_SAVE_FAILED')

    def revoke(self, actor):
        with self._lock():
            value = self._read()
            value['grants'].pop(actor, None)
            self._write(value)
