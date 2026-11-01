#!/usr/bin/env python3
"""Kristy music icon setup (one-time): uploads the 6 monochrome player icons to the Kristy
Discord app using the DISCORD_TOKEN already exported in this Termux session, then saves the
non-secret icon IDs to ~/shouko-bot/.env. Identity is verified against the existing music
command ledger before anything is uploaded; mismatches abort without changes.
Prints nothing secret. Run: python kristy-add-music-icons.py"""
import json, os, pathlib, sys, tempfile, urllib.request, urllib.error

API = 'https://discord.com/api/v10'
ICONS = {
    'kristy_play': 'iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAAB8ElEQVR4nO3cW47iQBBFwWQ0+9+y+6MnpUaiB2OKemRFbABL9yjtLyIAAAAAAAAAAAAAAAAAABo6juMY/QyV/Rn9AGcc/4x+joqWCCCJoL2lAohwDVpbLoAkhDaWDSAJ4T3LB5BEcE2ZACJcgytKBZCEcF7JAJIInisdQIRr8Ez5AJIQHtsmgCSEe9sFkETwbdsAIlyDiM0DSDuHIIAfdgxBAA/sFIEAfrHLNRDAE9VDEMBJVSMQwAsqXgMBXFApBAG8oUIIAmhg5QgE0Miq10AAja0WggA+ZJUIBPBBK1wDAXQwcwgC6GjGEAQwwEwRCGCQWa6BAAYbHYIAJjEqBAFMpncEAphQz2vwt8eP8Jrb7Xbr9VsuwGR6jh/hAkyj9/BJAIONGj4JYJDRwyffAAPMMn6EC9DVTMMnAXQw4/BJAB808/DJN8CHrDB+hAvQ3CrDJwE0strwySuggVXHj3AB3rLy8EkAF1QYPgngBZWGT74BTqo4foQL8FTV4ZMAflF9+OQV8MAu40e4AHd2Gj4JIPYcPm0dwM7Dp22/AYz/bbsLYPh72wRg+MfKB2D4/yv9DWD850peAMOfVyoAw7+uzCvA+NcsfwEM/55lAzB8G8sFYPi2lvoGMP6GZvgvPQAAAAAAAAAAAAAAAACYyBfCegBcWzO2YAAAAABJRU5ErkJggg==',
    'kristy_pause': 'iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAABYklEQVR4nO3TS3LCMBRFQTn737MzZ5DiY8ELp3tsVBf5eC0AAAAAAAAAAAAAAAAA4D85Pj3gXud5nvc+exzHtv81ZcdVxg985MJvXfkCpuy42s+nB/zllUu/4vfTduwwNoBve3lTIxgZwNWX9ex5U3bsNDIA3mdcALu+kkfPnbJjt3EB8F4CiBNAnADiBBAngDgBxAkgTgBxAogTQJwA4gQQJ4A4AcQJIE4AcQKIE0CcAOIEECeAOAHECSBOAHECiBNAnADiBBAngDgBxAkgTgBxAogTQJwA4gQQJ4A4AcQJIE4AcQKIE0CcAOIEECeAOAHECSBOAHECiBNAnADiBBAngDgBxAkgTgBxAogTQJwA4sYFcBzHMeHcKTt2GxcA7zUygKu/kmfPm7Jjp5EBrHXdZb16zpQdu4wNYK3veXlTX/5aa40ddus8z/PeZ3de+JQdAAAAAAAAAAAAAAAAAGT9ApsKWJDWPp86AAAAAElFTkSuQmCC',
    'kristy_skip': 'iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAACN0lEQVR4nO3cwXIaMRAE0CGV///lzSFRmYox7C5iJc28d/UBNN300REAAAAAAAAAAAAAAAAAwEpuoz5427btdrsN+/xRtm3bfvrbiHv8uvoD7z07Rkav3jviHkMLEPH30RWKsPeNV99ieAGazCU4+rYrbzFNASJyrsHZ91x1h6kK0GQrwcymLEBEzjWY0bQFaJTgs6YvQIQ1+KQlCtAoQX9LFSDCGvS2XAEaJehj2QJEWIMeli5AowTnpShAhDU4K00BGiU4Jl0BIqzBESkL0CjBa6kLEGENXklfgEYJHitTgAhr8EipAjRK8KVkASKsQVO2AE31EpQvQETtNVCAOxVLoAD/qbYGCvCDKiVQgCcqrIEC7JC5BAqwU9Y1UICDspVAAU7ItAYK8IYMJVCAN62+BgrQyaolUICOVlyD36O/QCYr/tMrC9DJiuFHWIC3rRp8YwHesHr4ERbglAzBNxbgoEzhR1iA3bIF31iAHbKGH2EBnsocfGMBflAh/AgL8E2V4BsLcKda+BEWICJqBt+UX4DK4UcUXoDqwTclF0D4X0otgOC/K7MAwn8s/QII/rnUCyD811IugOD3S7cAwj8mzQII/pwUCyD885ZeAMG/b9kFEH4fyy2A4PtaagGE398SCyD4z5l+AYT/WdMugOCvMeUCCP86UxXg9s/o79HT2fdcdYdpCpAt+HtH33blLYYXIOOv/pG9b7z6FkMLUCH4e6/eW+0eAAAAAAAAAAAAAAAAAJzyB0mSAIHXr9KSAAAAAElFTkSuQmCC',
    'kristy_queue': 'iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAABpklEQVR4nO3bQW7DMAwEQLn//7N7KAz00CCplFAmOfMAVwoXSqxixwAAAAAAAAAAAAAAAAAAAHI4di/g3c7zPHevYdZxHOHz+Ir+g5+Uefhj7Fl/mQBkH/4leh8lAlBl+JfI/Wz7DfDXJme+A6sN/7eI3wRbToBHQ6s8zLsKD8CzIQtBrNAAvDpcIYgTFoD/DlUIYpR4C2CeADQnAM0JQHNhAfjvpcaOf4x0FHoCvDpUw48T/hXwbLhOih9R+9ryG+DR5mY3XS0Ekfsp9cFVuDyKDnOpt4DsJ0H29QMAAABwY+VunjJfB+sGLso8/DF0A5dkH/5FN3BCleFfdAMXn1OFbiAfpxvYnG5gc7qBzZV4C2CeADQnAM0JQHO6gc3pBjanG3hTuoFveF5WuoGTKlwe6QYuyH4SZF8/AAAAADdW7uYp83WwbuCizMMfQzdwSfbhX3QDJ1QZ/kU3cPE5VegG8nG6gc3pBjanG9hcibcA5glAcwLQnAA0pxvYnG5gc7qBN6Ub+IbnZaUbOKnC5ZFu4ILsJ0H29QMAAAAAAAAAAAAAEO4brbrYaiJlfI4AAAAASUVORK5CYII=',
    'kristy_stop': 'iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAABQ0lEQVR4nO3dMQKCUAxEweD974w9hZWQ1Z05QZL/qJkBAAAAAAAAAAAAAAAAAAAA0h3bA3xynue5PcO3HMcReevIof7p4a/SQnhtD3D1z48/k7dfVABpx7lL0p4xASQd5Qkp+0YEkHKMpyXsHREAe9YDSPgKNm3vvx4AuwRQTgDlBFBOAOUEUE4A5QRQTgDlBFBOAOUEUE4A5QRQTgDlBFBOAOUEUE4A5QRQTgDlBFBOAOUEUE4A5QRQTgDlBFBOAOUEUE4A5QRQTgDlBFBOAOUEUE4A5QRQTgDlBFBOAOUEUE4A5QRQTgDlBFBOAOUEUE4A5QRQbj2AtB8pPm17//UA2BURwPZXsCVh74gAZjKO8aSUfWMCmMk5yt2S9owKYCbrOHdI2y9qmKvtnyp+U9rDAwAAAAAAAAAAAAAAAAAA8FPe2V80gBcke/YAAAAASUVORK5CYII=',
    'kristy_leave': 'iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAABW0lEQVR4nO3cUU7DMBBFUQex/y2HHypVfABS7Zk3cM4CKiu+dZ1E7loAAAAAAAAAAAAAAAAAAAAAAAAAAAAAf9h933f3GL5zdQ/gN6ou4nVd26/HY+wnPnuHt+4B/BepK4EACt2fusfxTAANkiIQQJOUCATQKCECATTrjkAAATo3hwII0hGBAMJURyCAQJURCCBUVQQCCFaxOYx8QfFV961SglMvk6wAQ5z6EghgkBMRCGCY3REIYKCdm0MBDLYjAgEM92oEAhju1dtDAQy249nA+46BUGvnQyErwDC7nwgKYJATj4MFMMSpdwH2AE9Ongx6xclTRQIIVnGczE9AqKqzhAIIVHmQVABhqk8RCyBIxxFym8AAnf8dYAVo1v3HEQJo1D35awmgTcLkryWAFimTv5ZNYKmkiX+wAhRJnHwKONUEAAAAAAAAAAAAAAAAAAAAAAAAAADADz4Af8JwQ6yv7XMAAAAASUVORK5CYII=',
}
ENV_KEYS = {'kristy_play': 'MUSIC_ICON_PLAY', 'kristy_pause': 'MUSIC_ICON_PAUSE', 'kristy_skip': 'MUSIC_ICON_SKIP',
            'kristy_queue': 'MUSIC_ICON_QUEUE', 'kristy_stop': 'MUSIC_ICON_STOP', 'kristy_leave': 'MUSIC_ICON_LEAVE'}

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
    lines = [l for l in old.splitlines() if not l.startswith('MUSIC_ICON_')]
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
    print('Saved icon IDs (values not shown). Restart Kristy with the usual command; player buttons will use the new icons.')

if __name__ == '__main__':
    main()
