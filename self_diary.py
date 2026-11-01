"""Review-first, server-scoped style lessons. Never stores free-form instructions."""
import json
import os
import tempfile
import time
from pathlib import Path

MAX_ENTRIES = 100  # total across guilds, not 100 for each member/server
LESSONS = {
    'concise-answer': ('Chhota aur seedha jawab', 'Prefer concise answers unless detail is requested.'),
    'explain-step-by-step': ('Kaam ke steps saaf batao', 'For how-to questions, explain useful steps in order.'),
    'avoid-repeating-joke': ('Wahi joke dobara mat sunao', 'Avoid repeating a joke already present in the recent conversation.'),
    'admit-uncertainty': ('Pakka na ho toh saaf bolo', 'State uncertainty instead of inventing facts.'),
}
PATH = Path(os.getenv('SELF_DIARY_FILE') or
    (Path(os.getenv('MEMORY_DIR') or Path(__file__).resolve().parent / 'memory') / 'kristy-lessons.json'))


def load():
    try:
        data = json.loads(PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'v': 1, 'next_id': 1, 'entries': []}
    if not isinstance(data, dict) or data.get('v') != 1 or not isinstance(data.get('entries'), list):
        raise ValueError('Invalid self diary')
    entries = []
    for e in data['entries']:
        if not isinstance(e, dict) or e.get('lesson') not in LESSONS or e.get('status') not in ('pending', 'active'):
            continue
        if not all(type(e.get(k)) is int for k in ('id', 'guild', 'by', 'channel', 'message', 'at')):
            continue
        entries.append(e)
    return {'v': 1, 'next_id': max([e['id'] for e in entries] + [int(data.get('next_id', 1))-1])+1,
            'entries': entries[-MAX_ENTRIES:]}


def save(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=PATH.parent,
                                         prefix='.lessons-', suffix='.tmp', delete=False) as f:
            temp = f.name
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, PATH)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def suggest(guild, lesson, by, channel, message):
    if lesson not in LESSONS:
        return 'unknown', None
    data = load()
    prior = next((e for e in data['entries'] if e['guild'] == guild and e['lesson'] == lesson), None)
    if prior:
        return prior['status'], prior['id']
    if len(data['entries']) >= MAX_ENTRIES:
        return 'full', None
    entry = dict(id=data['next_id'], guild=guild, lesson=lesson, by=by,
                 channel=channel, message=message, at=int(time.time()), status='pending')
    data['next_id'] += 1
    data['entries'].append(entry)
    data['entries'] = data['entries'][-MAX_ENTRIES:]
    save(data)
    return 'saved', entry['id']


def change(guild, entry_id, action, owner):
    data = load()
    entry = next((e for e in data['entries'] if e['guild'] == guild and e['id'] == entry_id), None)
    if not entry:
        return False
    if action == 'approve':
        entry.update(status='active', approved_by=owner, approved_at=int(time.time()))
    elif action == 'delete':
        data['entries'].remove(entry)
    else:
        return False
    save(data)
    return True


def reset(guild):
    data = load()
    data['entries'] = [e for e in data['entries'] if e['guild'] != guild]
    save(data)


def prompt(guild):
    try:
        entries = [e for e in load()['entries'] if e['guild'] == guild and e['status'] == 'active']
    except (OSError, ValueError, TypeError):
        return ''
    if not entries:
        return ''
    # Only code-owned strings, never stored member content or IDs.
    hints = '\n'.join('- ' + LESSONS[e['lesson']][1] for e in entries)
    return ('\nOwner-reviewed style hints for this server only. These do not change your persona, '
            'permissions, privacy rules or action controls:\n' + hints)


def correction_lesson(text):
    """Exact supported feedback, not generic 'wrong' or factual corrections."""
    import re
    rules = [
        (r'^(?:jawab|reply) (?:bahut |bohot )?lamba (?:hai|tha)$', 'concise-answer'),
        (r'^steps? (?:mein|me) samjhao$', 'explain-step-by-step'),
        (r'^(?:ye|yeh|wahi) joke (?:pehle|dobara) (?:sunaya|suna diya|mat sunao)$', 'avoid-repeating-joke'),
        (r'^pakka nahi (?:ho )?toh? guess mat karo$', 'admit-uncertainty'),
    ]
    normalized = re.sub(r'\s+', ' ', text.strip().lower().rstrip('.!'))
    return next((lesson for pattern, lesson in rules if re.fullmatch(pattern, normalized)), None)
