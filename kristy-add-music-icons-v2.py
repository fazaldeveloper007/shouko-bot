#!/usr/bin/env python3
"""Kristy music icons v2 (one-time): uploads 3 new monochrome icons (queue, leave, autoplay) to the Kristy Discord
app using the DISCORD_TOKEN already exported in this Termux session (or in ~/shouko-bot/.env), then updates ONLY
MUSIC_ICON_QUEUE, MUSIC_ICON_LEAVE and MUSIC_ICON_AUTOPLAY in ~/shouko-bot/.env. The other icon IDs are kept.
Old uploaded emoji are left untouched. Identity is verified against the music command ledger first; mismatches
abort without changes. Prints nothing secret. Run: python kristy-add-music-icons-v2.py"""
import json, os, pathlib, sys, tempfile, urllib.request, urllib.error

API = 'https://discord.com/api/v10'
ICONS = {'kristy_queue_v2': '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', 'kristy_leave_v2': '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', 'kristy_autoplay': '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'}
ENV_KEYS = {'kristy_queue_v2': 'MUSIC_ICON_QUEUE', 'kristy_leave_v2': 'MUSIC_ICON_LEAVE', 'kristy_autoplay': 'MUSIC_ICON_AUTOPLAY'}

def fail(msg):
    sys.exit('ABORTED: ' + msg)

def req(method, path, token, payload=None, uploaded=None, total=None):
    r = urllib.request.Request(API + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bot ' + token, 'Content-Type': 'application/json',
                 'User-Agent': 'DiscordBot (kristy-icon-setup, 1.0)'})
    try:
        with urllib.request.urlopen(r) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        e.read()
        note = '' if uploaded is None else (f' {uploaded} of {total} icons were already uploaded;'
                                            ' .env was not changed and re-running is safe.')
        fail(f'Discord API returned HTTP {e.code} on {method} {path}.{note}'
             ' Check the exported token, then retry.')
    except urllib.error.URLError:
        note = '' if uploaded is None else (f' {uploaded} of {total} icons were already uploaded;'
                                            ' .env was not changed and re-running is safe.')
        fail('No connection to Discord.' + note + ' Check internet, then retry.')

def main():
    app_dir = pathlib.Path.home() / 'shouko-bot'
    ledger = app_dir / '.music-command-ownership.json'
    env_path = app_dir / '.env'
    token = os.environ.get('DISCORD_TOKEN', '').strip()
    if not token and env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('DISCORD_TOKEN='):
                token = line.split('=', 1)[1].strip().strip('"').strip("'")
    if not token:
        fail('DISCORD_TOKEN not found in this session or in ~/shouko-bot/.env. Export it first, then re-run this file.')
    if not ledger.exists():
        fail(f'{ledger} not found. Start Kristy once with command sync first (or ask Instinct), then re-run.')
    try:
        expected = json.loads(ledger.read_text()).get('application_id')
    except Exception:
        fail('Could not read the music command ledger.')
    status, app = req('GET', '/oauth2/applications/@me', token)
    app_id = str(app.get('id', ''))
    if not expected or str(expected) != app_id:
        fail('Token belongs to a different app than the registered music commands. Not touching anything.')
    print(f"App verified: {app.get('name')} - matches the registered music commands.")
    status, data = req('GET', f'/applications/{app_id}/emojis', token)
    existing = {e['name']: e for e in data.get('items', [])}
    own_ids = {x for x in (app_id, str((app.get('bot') or {}).get('id', ''))) if x}
    foreign = [n for n in ICONS if n in existing
               and str((existing[n].get('user') or {}).get('id', '')) not in own_ids]
    if foreign:
        fail('Emoji name(s) already used by a different upload: ' + ', '.join(foreign)
             + '. Nothing was uploaded or changed. Ask Instinct before replacing them.')
    ids = {}
    uploaded = 0
    todo = [(n, b) for n, b in ICONS.items() if n not in existing]
    for name in existing:
        if name in ICONS:
            ids[name] = existing[name]['id']
            print(f'{name}: already on the app, reusing it')
    for name, b64 in todo:
        status, created = req('POST', f'/applications/{app_id}/emojis', token,
                              {'name': name, 'image': 'data:image/png;base64,' + b64},
                              uploaded=uploaded, total=len(todo))
        ids[name] = created['id']
        uploaded += 1
        print(f'{name}: uploaded')
    old = env_path.read_text() if env_path.exists() else ''
    drop = tuple(v + '=' for v in ENV_KEYS.values())
    lines = [l for l in old.splitlines() if not l.startswith(drop)]
    lines += [f'{ENV_KEYS[n]}={n}:{i}' for n, i in ids.items()]
    fd, tmp = tempfile.mkstemp(dir=str(app_dir), prefix='.env.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        os.chmod(tmp, 0o600)
        os.replace(tmp, env_path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        fail('Could not save icon IDs to .env.')
    print('Saved icon IDs (values not shown). Restart Kristy with the usual command; queue, leave and autoplay buttons will use the new icons.')

if __name__ == '__main__':
    main()
