"""Dry-run routing check: real Gemini, synthetic context, zero Discord.

Run on the phone in the repo directory:

    python dryrun_routing.py

Credentials come from the exact path the bot itself uses (bot.py:65-84 -
load_dotenv() then os.environ), so .env just works. The key is never
printed, logged, or stored anywhere new.

Each scenario spends ONE free-tier Gemini call (6 scenarios = 6 calls of
the shared daily budget). Nothing reaches Discord: the gateway never
connects and actions run against in-memory fakes; announce stops at its
preview by design, so nothing can be posted. The point is to verify what
the offline harness cannot: whether the real model maps natural Hinglish
requests to the right action marker (or a clarifying question) and leaves
normal chat alone.
"""
import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock

if not (os.environ.get('GEMINI_API_KEY') or '').strip():
    # .env is loaded by bot.py at import; this early exit only catches a
    # missing key before we spend time building the client
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
if not (os.environ.get('GEMINI_API_KEY') or '').strip():
    sys.exit('GEMINI_API_KEY not set (.env missing?) - no calls made.')

import actions  # noqa: E402
import bot  # noqa: E402  module import builds the client but never logs in

CHAN = 424242
OWNER = (os.environ.get('OWNER_ID') or '1').strip()
CALLS = 0


def fake_message(author_id, channel_name='general'):
    channel = types.SimpleNamespace(
        id=CHAN, name=channel_name, send=AsyncMock(),
        permissions_for=lambda me: types.SimpleNamespace(mention_everyone=True))
    guild = types.SimpleNamespace(id=777, me=None, text_channels=[channel])
    author = types.SimpleNamespace(id=int(author_id), voice=None)
    return types.SimpleNamespace(author=author, channel=channel, guild=guild,
                                 reply=AsyncMock())


async def ask(label, asker_line, seeds=(), expect=None, note=''):
    """One scenario: seed history, let the real model reply, classify."""
    global CALLS
    bot.history.pop(CHAN, None)
    for author, content in seeds:
        bot.record_message(CHAN, author, 2, content)
    bot.record_message(CHAN, 'Fazal', int(OWNER), asker_line)
    reply = await bot.think(CHAN)
    CALLS += 1
    m = actions.DO_RE.match(reply or '')
    if not m:
        verdict = 'PASS' if expect is None else 'WARN'
        print(f'[{verdict}] {label}: plain reply (no action) - {reply[:90]!r}')
        return
    cmd, chan = m.group(1).lower(), m.group(2)
    verdict = 'PASS' if expect in (cmd, None) else 'WARN'
    print(f'[{verdict}] {label}: marker [[do:{cmd}'
          + (f' #{chan}' if chan else '') + ']]'
          + (f'  ({note})' if note else ''))
    # execute against fakes: proves validation + composed outcome, no Discord
    fake_client = types.SimpleNamespace(music=None, get_channel=lambda cid: None)
    act = actions.Actions(fake_client, OWNER, types.SimpleNamespace(load=lambda: []))
    out = await act.maybe_execute(fake_message(OWNER), reply)
    print(f'        -> code outcome: {out[:110]!r}')


async def main():
    await ask('normal chat', 'kaisi ho?', expect=None)
    await ask('announce w/ context', 'is channel mein ek announcement dhal do everyone tag karke us baare mein',
              seeds=[('UG', 'scrim saturday 8 baje rakhte hai'), ('Fazal', 'haan final, sab ko bata dena')],
              expect='announce everyone',
              note='draft should mention scrim/saturday from context')
    await ask('announce unclear scope', 'ek announcement daal do scrim ke baare mein',
              note='bare announce -> code asks everyone vs quiet; scoped is also fine')
    await ask('cross-channel target', '#events mein announcement daal do tournament ke baare mein',
              note='marker should carry #events; unknown names get a clarify')
    await ask('music status', 'kya baj raha hai abhi?', expect='music status')
    await ask('reminder list', 'mere reminders dikha do', expect='reminder list')
    print(f'\n{CALLS} Gemini calls spent. No Discord connection was made;'
          ' nothing was posted anywhere.')
    await bot.client.close()


if __name__ == '__main__':
    asyncio.run(main())
