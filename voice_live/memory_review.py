"""Explicit private first-run review. Running preview sends nothing to Google.

The numbered picker displays existing notes privately in the local terminal.
Nothing is selected by default. Only explicit choices plus confirmation create
a digest selection file; canonical diary stays unchanged. No network calls.
The optional legacy preview exports a private0600 JSON file, not stdout.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
import sys
import unicodedata
import re
import diary  # Same import-time canonical directory as text bot, before dotenv.
from voice_live.memory import Selection, MemoryErrorCode, digest, identity
from voice_live.member_store import MemberStore


def private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.voice-review-')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp and Path(temp).exists():
            Path(temp).unlink()


def preview(store, owner, guild, path):
    identity(owner)
    identity(guild)
    generation, data = store.snapshot(owner)
    document = {'owner': owner, 'guild': guild, 'generation': generation,
        'notice': 'Private local review only. No notes selected or sent. Keep only your own non-sensitive notes; no other-person facts. Put only chosen digests in approved_digests. Delete this preview after selection.',
        'notes': [{'digest': digest(item['t']), 'text': item['t']} for item in data['facts']],
        'approved_digests': []}
    # Preferred name is not silently imported because older schema lacks its
    # provenance/review binding. It can be supplied as an ordinary reviewed fact.
    private_json(path, document)
    return len(document['notes'])


def approve(store, owner, guild, preview_path, selection_path):
    raw = Path(preview_path)
    if raw.stat().st_size > 65536:
        raise MemoryErrorCode('MEMORY_REVIEW_INVALID')
    try:
        data = json.loads(raw.read_text(encoding='utf-8'))
        generation, current = store.snapshot(owner)
        if (data['owner'] != identity(owner) or data['guild'] != identity(guild)
                or data['generation'] != generation
                or not isinstance(data['approved_digests'], list)):
            raise MemoryErrorCode('MEMORY_SCOPE_CHANGED')
        selection = Selection(owner, guild, generation, tuple(data['approved_digests']))
        current_digests = {digest(item['t']) for item in current['facts']}
        if not set(selection.digests) <= current_digests:
            raise MemoryErrorCode('MEMORY_SELECTION_STALE')
        private_json(selection_path, {'owner': owner, 'guild': guild,
            'generation': generation, 'digests': list(selection.digests)})
        return selection
    except (ValueError, TypeError, KeyError):
        raise MemoryErrorCode('MEMORY_REVIEW_INVALID') from None


def load_selection(path):
    p = Path(path)
    try:
        if p.stat().st_size > 8192:
            raise MemoryErrorCode('MEMORY_SELECTION_INVALID')
        data = json.loads(p.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) != {'owner', 'guild', 'generation', 'digests'}:
            raise MemoryErrorCode('MEMORY_SELECTION_INVALID')
        return Selection(data['owner'], data['guild'], data['generation'], tuple(data['digests']))
    except (OSError, ValueError, TypeError, KeyError):
        raise MemoryErrorCode('MEMORY_SELECTION_INVALID') from None


def display_text(text):
    # Preserve readable ordinary Unicode but prevent terminal escape/bidi/control
    # effects from stored notes. This is DISPLAY only, never changes note digest.
    return ''.join(c if not unicodedata.category(c).startswith('C')
                   else ('\\u%04x' % ord(c)) for c in text)


def parse_numbers(raw, count):
    if not isinstance(raw, str) or len(raw) > 256:
        raise MemoryErrorCode('MEMORY_PICK_INVALID')
    raw = raw.strip()
    if raw.lower() == 'none':
        return ()
    if not raw or not re.fullmatch(r'[0-9]+(?:(?: +| *, *)[0-9]+)*', raw):
        raise MemoryErrorCode('MEMORY_PICK_INVALID')
    numbers = tuple(int(x) for x in re.split(r'[ ,]+', raw))
    if len(set(numbers)) != len(numbers) or any(n < 1 or n > count for n in numbers):
        raise MemoryErrorCode('MEMORY_PICK_INVALID')
    return numbers


def pick(store, owner, guild, selection_path, *, read=input, write=print, private_terminal=None):
    identity(owner)
    identity(guild)
    if private_terminal is None:
        private_terminal = sys.stdin.isatty() and sys.stdout.isatty()
    if private_terminal is not True:
        raise MemoryErrorCode('MEMORY_PRIVATE_TERMINAL_REQUIRED')
    generation, snapshot = store.snapshot(owner)
    notes = snapshot['facts']
    if not notes:
        write('MEMORY_NO_EXISTING_NOTES: no selection changed. Check MEMORY_DIR if unexpected.')
        return None
    write('PRIVATE LOCAL REVIEW: existing own-user diary notes. No Google/VC activity.')
    write('Do not screenshot/share this private screen. Terminal scrollback may retain displayed notes.')
    write('Choose only your relevant non-sensitive self-facts, no private details or other-person facts.')
    for number, item in enumerate(notes, 1):
        write(str(number) + '. ' + display_text(item['t']))
    write('Enter chosen numbers, e.g. 1 3 5, or NONE for zero notes. Empty/CANCEL discards. Nothing is selected by default.')
    raw = read()
    if not raw.strip() or raw.strip().lower() == 'cancel':
        write('MEMORY_SELECTION_CANCELLED: previous selection unchanged.')
        return None
    numbers = parse_numbers(raw, len(notes))
    write('Chosen note numbers: ' + (', '.join(map(str, numbers)) if numbers else 'NONE'))
    write('Type CONFIRM to allow only these notes in this restricted voice context. Anything else cancels. No Google call now.')
    if read().strip() != 'CONFIRM':
        write('MEMORY_SELECTION_CANCELLED: previous selection unchanged.')
        return None
    current_generation, current = store.snapshot(owner)
    selection = Selection(owner, guild, generation,
        tuple(digest(notes[n-1]['t']) for n in numbers))
    if current_generation != generation:
        raise MemoryErrorCode('MEMORY_SCOPE_CHANGED')
    available = {digest(item['t']) for item in current['facts']}
    if not set(selection.digests) <= available:
        raise MemoryErrorCode('MEMORY_SELECTION_STALE')
    private_json(selection_path, {'owner': owner, 'guild': guild,
        'generation': generation, 'digests': list(selection.digests)})
    write('MEMORY_SELECTION_READY count=' + str(len(selection.digests)))
    return selection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pick', 'preview', 'approve', 'revoke'))
    parser.add_argument('--user-id', type=int, required=True)
    parser.add_argument('--guild-id', type=int, required=True)
    parser.add_argument('--preview-file', type=Path)
    parser.add_argument('--selection-file', type=Path)
    parser.add_argument('--confirmed-private-own-notes', action='store_true')
    args = parser.parse_args()
    directory = Path(diary.MEMORY_DIR)
    if args.action in ('pick', 'preview', 'approve') and not (directory / f'{args.user_id}.json').is_file():
        raise MemoryErrorCode('MEMORY_EXISTING_DIARY_NOT_FOUND')
    store = MemberStore(directory)
    if args.action == 'pick':
        selection_path = args.selection_file or (directory / f'voice-selection-{args.user_id}-{args.guild_id}.json')
        pick(store, args.user_id, args.guild_id, selection_path)
    elif args.action == 'revoke':
        store.revoke(args.user_id)
        print('MEMORY_ACCESS_REVOKED')
    elif args.action == 'preview':
        if args.preview_file is None:
            raise MemoryErrorCode('MEMORY_REVIEW_REQUIRED')
        count = preview(store, args.user_id, args.guild_id, args.preview_file)
        print('MEMORY_PRIVATE_PREVIEW_READY count=' + str(count))
    else:
        if not args.confirmed_private_own_notes or args.selection_file is None or args.preview_file is None:
            raise MemoryErrorCode('MEMORY_REVIEW_REQUIRED')
        selection = approve(store, args.user_id, args.guild_id, args.preview_file, args.selection_file)
        print('MEMORY_SELECTION_READY count=' + str(len(selection.digests)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if type(error) is MemoryErrorCode else 'MEMORY_REVIEW_FAILED')
        raise SystemExit(1)
