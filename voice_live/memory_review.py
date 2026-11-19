"""Explicit private first-run review. Running preview sends nothing to Google.

Preview stays in a mode0600 file, never stdout. User manually lists the digests
of notes approved as non-sensitive own-user facts for this restricted VC.
No automatic selection and no actual note access occurs merely by import.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preview', 'approve', 'revoke'))
    parser.add_argument('--user-id', type=int, required=True)
    parser.add_argument('--guild-id', type=int, required=True)
    parser.add_argument('--preview-file', type=Path, required=True)
    parser.add_argument('--selection-file', type=Path)
    parser.add_argument('--confirmed-private-own-notes', action='store_true')
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    import diary
    directory = Path(os.getenv('MEMORY_DIR') or diary.MEMORY_DIR).expanduser()
    store = MemberStore(directory)
    if args.action == 'revoke':
        store.revoke(args.user_id)
        print('MEMORY_ACCESS_REVOKED')
    elif args.action == 'preview':
        count = preview(store, args.user_id, args.guild_id, args.preview_file)
        print('MEMORY_PRIVATE_PREVIEW_READY count=' + str(count))
    else:
        if not args.confirmed_private_own_notes or args.selection_file is None:
            raise MemoryErrorCode('MEMORY_REVIEW_REQUIRED')
        selection = approve(store, args.user_id, args.guild_id, args.preview_file, args.selection_file)
        print('MEMORY_SELECTION_READY count=' + str(len(selection.digests)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if type(error) is MemoryErrorCode else 'MEMORY_REVIEW_FAILED')
        raise SystemExit(1)
