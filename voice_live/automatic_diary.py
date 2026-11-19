"""Approved own-ID diary read scope. Does not select/read other members."""
import json
from pathlib import Path
from voice_live.memory import Selection, MemoryErrorCode, digest
from voice_live.memory_review import private_json

OWNER = 1217032327260274773
GUILD = 1217387325193982053

def authorize(store, owner, guild):
    if owner != OWNER or guild != GUILD:
        raise MemoryErrorCode('MEMORY_AUTOMATIC_SCOPE_NOT_APPROVED')
    if not (store.directory / f'{owner}.json').is_file():
        raise MemoryErrorCode('MEMORY_EXISTING_DIARY_NOT_FOUND')
    generation, snapshot = store.snapshot(owner)
    scope = store.directory / f'voice-automatic-{owner}-{guild}.json'
    expected = {'owner': owner, 'guild': guild, 'generation': generation, 'mode': 'own_diary'}
    if scope.exists():
        try:
            if scope.stat().st_size > 512 or json.loads(scope.read_text()) != expected:
                raise ValueError()
        except Exception:
            raise MemoryErrorCode('MEMORY_AUTOMATIC_ACCESS_REVOKED') from None
    else:
        # A prior forget/revoke is not undone by an absent access record.
        if generation != 0:
            raise MemoryErrorCode('MEMORY_AUTOMATIC_ACCESS_REVOKED')
        private_json(scope, expected)
    return Selection(owner, guild, generation, tuple(dict.fromkeys(digest(f['t']) for f in snapshot['facts'])))

def validate(store, owner, guild, generation):
    current = authorize(store, owner, guild)
    if current.generation != generation:
        raise MemoryErrorCode('MEMORY_SCOPE_CHANGED')
    return current
