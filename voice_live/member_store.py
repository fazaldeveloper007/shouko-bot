"""Shared member-store transactions for text and opt-in voice runners.

POSIX locks support Termux without a new dependency. Every writer/forget path
must use this boundary. External legacy scripts that call diary.save directly
are not covered by this lock.
"""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import tempfile

from voice_live.memory import identity, fact_text, MemoryErrorCode


class MemberStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    @contextlib.contextmanager
    def _locked(self, owner):
        identity(owner)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (str(owner) + '.memory.lock')
        with open(path, 'a', encoding='utf-8') as lock:
            # Caller must use to_thread in an async runner: never block audio loop.
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _atomic(self, path, data):
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=self.directory, prefix='.member-', suffix='.tmp', delete=False) as f:
                temp = f.name
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            if temp is not None:
                try:
                    os.unlink(temp)
                except FileNotFoundError:
                    pass

    def _generation(self, owner):
        path = self.directory / (str(owner) + '.memory-generation.json')
        try:
            if path.stat().st_size > 128:
                raise MemoryErrorCode('MEMORY_STORE_INVALID')
            data = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return 0
        except (OSError, ValueError):
            raise MemoryErrorCode('MEMORY_STORE_INVALID') from None
        if type(data) is not int or data < 0:
            raise MemoryErrorCode('MEMORY_STORE_INVALID')
        return data

    def _read(self, owner):
        path = self.directory / (str(owner) + '.json')
        try:
            if path.stat().st_size > 65536:
                raise MemoryErrorCode('MEMORY_STORE_INVALID')
            data = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'v': 1, 'name': '', 'facts': [], 'updated': 0}
        except (OSError, ValueError):
            raise MemoryErrorCode('MEMORY_STORE_INVALID') from None
        if (not isinstance(data, dict) or data.get('v') != 1
                or not isinstance(data.get('name'), str) or len(data['name']) > 40
                or not isinstance(data.get('facts'), list) or len(data['facts']) > 40):
            raise MemoryErrorCode('MEMORY_STORE_INVALID')
        for item in data['facts']:
            if (not isinstance(item, dict) or not isinstance(item.get('ts'), (float, int))
                    or isinstance(item.get('ts'), bool)):
                raise MemoryErrorCode('MEMORY_STORE_INVALID')
            fact_text(item.get('t'))
        return data

    def generation(self, owner):
        with self._locked(owner):
            return self._generation(owner)

    def snapshot(self, owner):
        with self._locked(owner):
            return self._generation(owner), self._read(owner)

    def merge_reviewed(self, owner, *, expected_generation, facts, merge, name=None):
        """ONLY caller-reviewed facts, not raw extractor output.

        `merge` is canonical diary.merge, injected to preserve existing format and
        duplicate/pruning rules without importing bot.py or any network client.
        Optional preferred-name writes preserve the existing text extractor behavior. No file path comes from model output.
        """
        if type(expected_generation) is not int or expected_generation < 0:
            raise MemoryErrorCode('MEMORY_GENERATION_INVALID')
        if name is not None and (not isinstance(name, str) or len(name) > 40
                                 or any(ord(c) < 32 for c in name)):
            raise MemoryErrorCode('MEMORY_FACT_INVALID')
        if not isinstance(facts, tuple) or len(facts) > 8:
            raise MemoryErrorCode('MEMORY_FACTS_INVALID')
        for text in facts:
            fact_text(text)
        with self._locked(owner):
            if self._generation(owner) != expected_generation:
                raise MemoryErrorCode('MEMORY_SCOPE_CHANGED')
            data = self._read(owner)
            merge(data, list(facts), name=name)
            path = self.directory / (str(owner) + '.json')
            try:
                self._atomic(path, data)
                # Report save only after actual persisted content is readable.
                return self._read(owner)
            except OSError:
                raise MemoryErrorCode('MEMORY_SAVE_FAILED') from None

    def revoke(self, owner):
        """Invalidate sessions/in-flight writes without deleting member facts."""
        with self._locked(owner):
            generation = self._generation(owner) + 1
            try:
                self._atomic(self.directory / (str(owner) + '.memory-generation.json'), generation)
            except OSError:
                raise MemoryErrorCode('MEMORY_REVOKE_FAILED') from None
            return generation

    def forget(self, owner):
        """Bump generation BEFORE removal, so late extraction cannot resurrect it.

        A failed deletion is not success. A bumped generation on failure still
        invalidates pre-existing sessions and in-flight writes.
        """
        with self._locked(owner):
            generation = self._generation(owner) + 1
            try:
                self._atomic(self.directory / (str(owner) + '.memory-generation.json'), generation)
                for suffix in ('.json', '.json.tmp'):
                    (self.directory / (str(owner) + suffix)).unlink(missing_ok=True)
            except OSError:
                raise MemoryErrorCode('MEMORY_DELETE_FAILED') from None
            return generation
