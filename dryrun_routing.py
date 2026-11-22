"""Dry-run routing check: real Gemini, synthetic context, zero Discord.

Run on the phone in a SEPARATE clone of the agent-actions branch (never in
the running production directory):

    cd ~ && git clone --single-branch --branch agent-actions \
        https://github.com/fazaldeveloper007/shouko-bot kristy-test
    cd kristy-test
    ln -s /path/to/production/.env .env   # symlink: never copies or prints the key
    python dryrun_routing.py

Credentials come from the exact path the bot itself uses (bot.py:65-84 -
load_dotenv() then os.environ), so the symlinked .env just works. The key
is never printed, logged, or stored anywhere new.

What it proves that the offline harness cannot: whether the REAL model
(read from your actual GEMINI_MODEL config, printed at start) maps natural
Hinglish requests to the right action marker with the right topic, target
and mention scope - and leaves normal chat alone. Each scenario checks the
OUTCOME (topic words from the seeded chat must appear in the draft; the
named channel must be carried; an unintended action on plain chat FAILS),
not merely that some marker exists.

Cost is bounded: one think() call per scenario, and the HTTP layer retries
at most 3 attempts per call (retrying.py), so 6 scenarios spend 6 calls
nominally, 18 worst case, paced 15s apart. Nothing reaches Discord: the
gateway never connects and actions run against in-memory fakes; announce
stops at its preview by design. Nothing persists: history seeds are
in-memory only (recap can only trigger on a full deque - 3 seed lines never
fill it), logging goes to the terminal only (bot.py:80), and the clone's
empty memory dir is used, never production's.
"""
import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock

if not (os.environ.get('GEMINI_API_KEY') or '').strip():
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
MAX_HTTP = 20  # paranoia cap: 6 scenarios x 3 retries = 18 max
PACE_SECONDS = 15

_http_calls = 0
_real_generate = bot.gemini_generate


async def _counting_generate(*args, **kwargs):
    global _http_calls
    _http_calls += 1
    if _http_calls > MAX_HTTP:
        raise RuntimeError('call budget exceeded; aborting dry run')
    return await _real_generate(*args, **kwargs)


bot.gemini_generate = _counting_generate


def fake_message(author_id, channel_name='general'):
    channel = types.SimpleNamespace(
        id=CHAN, name=channel_name, send=AsyncMock(),
        permissions_for=lambda me: types.SimpleNamespace(mention_everyone=True))
    guild = types.SimpleNamespace(id=777, me=None, text_channels=[channel])
    author = types.SimpleNamespace(id=int(author_id), voice=None)
    return types.SimpleNamespace(author=author, channel=channel, guild=guild,
                                 reply=AsyncMock())


async def execute_against_fakes(reply):
    fake_client = types.SimpleNamespace(music=None, get_channel=lambda cid: None)
    act = actions.Actions(fake_client, OWNER, types.SimpleNamespace(load=lambda: []))
    return await act.maybe_execute(fake_message(OWNER), reply)


async def scenario(label, asker_line, judge, seeds=()):
    """One scenario: seed synthetic history, real model reply, judge outcome."""
    bot.history.pop(CHAN, None)  # fresh deque; 3 seeds never trigger recap
    for author, content in seeds:
        bot.record_message(CHAN, author, 2, content)
    bot.record_message(CHAN, 'Fazal', int(OWNER), asker_line)
    reply = await bot.think(CHAN)
    m = actions.DO_RE.match(reply or '')
    cmd = m.group(1).lower() if m else None
    chan = m.group(2) if m else None
    draft = (m.group(3) or '') if m else ''
    verdict, why = judge(cmd, chan, draft, reply or '')
    print(f'[{verdict}] {label}: {why}')
    if m:
        print(f'        marker [[do:{cmd}' + (f' #{chan}' if chan else '') + ']]'
              + (f' | {draft[:60]!r}' if draft else ''))
        out = await execute_against_fakes(reply)
        print(f'        -> code outcome: {out[:110]!r}')
    else:
        print(f'        plain reply: {(reply or "")[:90]!r}')
    await asyncio.sleep(PACE_SECONDS)


async def _cleanup():
    # close the shared AI-API session (gemini and groq calls share it) and
    # the discord client, so the script exits without unclosed-session noise
    if bot._groq_session is not None and not bot._groq_session.closed:
        await bot._groq_session.close()
    await bot.client.close()


async def main():
    print(f'model under test: {bot.GEMINI_MODEL} (from your config)\n')

    def j_chat(cmd, chan, draft, reply):
        if cmd:
            return 'FAIL', f'unintended action on plain chat: [[do:{cmd}]]'
        return 'PASS', 'plain chat stayed plain'

    def j_announce_ctx(cmd, chan, draft, reply):
        if cmd != 'announce everyone':
            return 'FAIL', f'expected announce everyone marker, got {cmd or "no marker"}'
        if 'scrim' not in draft.lower():
            return 'FAIL', f'draft does not mention the seeded topic (scrim): {draft[:60]!r}'
        return 'PASS', 'right action, everyone scope, draft built from context'

    def j_unclear(cmd, chan, draft, reply):
        if cmd == 'announce':
            return 'PASS', 'scope left open -> code will ask everyone vs quiet'
        if cmd in ('announce everyone', 'announce quiet'):
            return 'PASS', f'model chose {cmd}; owner still reviews exact preview'
        if cmd is None and '?' in reply:
            return 'PASS', 'model asked a clarifying question instead'
        return 'WARN', f'unexpected: {cmd or "no marker, no question"}'

    def j_cross(cmd, chan, draft, reply):
        if cmd and cmd.startswith('announce') and (chan or '').lower() == 'events':
            return 'PASS', 'target channel #events carried into the marker'
        if cmd and cmd.startswith('announce'):
            return 'FAIL', 'channel target dropped - would silently bind current channel'
        return 'WARN', f'no announce marker: {(reply or "")[:60]!r}'

    def j_expect(want):
        def j(cmd, chan, draft, reply):
            if cmd == want:
                return 'PASS', f'routed to [[do:{want}]]'
            return 'FAIL', f'expected [[do:{want}]], got {cmd or "no marker"}'
        return j

    await scenario('normal chat', 'kaisi ho?', j_chat)
    await scenario('announce w/ context',
                   'is channel mein ek announcement dhal do everyone tag karke us baare mein',
                   j_announce_ctx,
                   seeds=[('UG', 'scrim saturday 8 baje rakhte hai'),
                          ('Fazal', 'haan final, sab ko bata dena')])
    await scenario('announce unclear scope',
                   'ek announcement daal do scrim ke baare mein', j_unclear)
    await scenario('cross-channel target',
                   '#events mein announcement daal do tournament ke baare mein', j_cross)
    await scenario('music status', 'kya baj raha hai abhi?', j_expect('music status'))
    await scenario('reminder list', 'mere reminders dikha do', j_expect('reminder list'))

    print(f'\n{_http_calls} Gemini HTTP attempts spent '
          f'(6 nominal, 18 worst case). Model: {bot.GEMINI_MODEL}.')
    print('No Discord connection was made; nothing was posted or persisted.')


async def run():
    try:
        await main()
    finally:
        await _cleanup()


if __name__ == '__main__':
    asyncio.run(run())
