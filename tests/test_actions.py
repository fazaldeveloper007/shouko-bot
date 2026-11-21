import asyncio
import pathlib
import time
import types
import unittest
from unittest.mock import AsyncMock

import actions

OWNER = '111111111111111111'
OTHER = '222222222222222222'


def make_message(*, author_id=OWNER, channel_id=20, guild_id=10, in_vc=True, can_everyone=True):
    channel = types.SimpleNamespace(
        id=channel_id, name='music', send=AsyncMock(),
        permissions_for=lambda me: types.SimpleNamespace(mention_everyone=can_everyone))
    guild = types.SimpleNamespace(id=guild_id, me=types.SimpleNamespace())
    voice = types.SimpleNamespace(channel=channel if in_vc else None)
    # caller_channel checks isinstance(vc, discord.VoiceChannel); patch via monkey attribute
    author = types.SimpleNamespace(id=int(author_id), voice=voice)
    return types.SimpleNamespace(author=author, channel=channel, guild=guild,
                                 reply=AsyncMock())


def make_actions(*, player=None, entries=(), can_everyone=True):
    manager = types.SimpleNamespace(
        caller_channel=lambda user, ch: ch if can_everyone or True else None,
        players={10: player} if player is not None else {})
    # caller_channel must return an object with guild.id
    manager.caller_channel = lambda user, ch: types.SimpleNamespace(guild=types.SimpleNamespace(id=10))
    client = types.SimpleNamespace(music=types.SimpleNamespace(manager=manager),
                                   get_channel=lambda cid: sent_channel.get(cid))
    store = types.SimpleNamespace(load=lambda: list(entries))
    return actions.Actions(client, OWNER, store)


sent_channel = {}


def track(title):
    return types.SimpleNamespace(title=title)


class MarkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_marker_passthrough(self):
        a = make_actions()
        self.assertEqual(await a.maybe_execute(make_message(), 'normal reply'), 'normal reply')

    async def test_unknown_marker_is_not_claimed(self):
        a = make_actions()
        out = await a.maybe_execute(make_message(), '[[do:fly to moon]]')
        self.assertIn('samjhi nahi', out)

    async def test_music_status_idle(self):
        a = make_actions()
        out = await a.maybe_execute(make_message(), '[[do:music status]]')
        self.assertIn('khaali', out)

    async def test_music_status_playing_with_queue(self):
        player = types.SimpleNamespace(closed=False, current=track('Song One'), paused=False,
                                       queue=[track('Song Two'), track('Song Three')], autoplay=True)
        a = make_actions(player=player)
        out = await a.maybe_execute(make_message(), '[[do:music status]]')
        self.assertIn('Song One', out)
        self.assertIn('playing', out)
        self.assertIn('Song Two', out)
        self.assertIn('autoplay', out)

    async def test_music_pause_nothing_playing(self):
        player = types.SimpleNamespace(closed=False, current=None, paused=False, queue=[], autoplay=False)
        a = make_actions(player=player)
        out = await a.maybe_execute(make_message(), '[[do:music pause]]')
        self.assertIn('koi track nahi', out)

    async def test_music_pause_executes(self):
        async def pause_flips(paused):
            player.paused = paused
        player = types.SimpleNamespace(closed=False, current=track('S'), paused=False, queue=[], autoplay=False,
                                       pause=AsyncMock(side_effect=pause_flips))
        a = make_actions(player=player)
        out = await a.maybe_execute(make_message(), '[[do:music pause]]')
        player.pause.assert_awaited_once_with(True)
        self.assertIn('pause kar diya', out)

    async def test_music_skip_reports_next(self):
        player = types.SimpleNamespace(closed=False, current=track('S'), paused=False,
                                       queue=[track('Next Song')], autoplay=False, interrupt=AsyncMock())
        a = make_actions(player=player)
        out = await a.maybe_execute(make_message(), '[[do:music skip]]')
        player.interrupt.assert_awaited_once_with()
        self.assertIn('Next Song', out)
        self.assertIn('shuru ho raha hai', out)
        self.assertNotIn('ab next:', out)  # never claims the next track is already playing

    async def test_music_stop_clears(self):
        async def stop_clears(clear=False):
            player.current = None; player.queue.clear()
        player = types.SimpleNamespace(closed=False, current=track('S'), paused=False,
                                       queue=[track('Q')], autoplay=False, interrupt=AsyncMock(side_effect=stop_clears))
        a = make_actions(player=player)
        out = await a.maybe_execute(make_message(), '[[do:music stop]]')
        player.interrupt.assert_awaited_once_with(clear=True)
        self.assertIn('queue clear', out)

    async def test_reminder_list_own_only(self):
        entries = [
            {'id': 'aaa111bbb2', 'guild': 10, 'author': int(OWNER), 'due': 1893456000, 'status': 'scheduled', 'reason': 'confirmed'},
            {'id': 'ccc333ddd4', 'guild': 10, 'author': int(OTHER), 'due': 1893456000, 'status': 'scheduled', 'reason': 'confirmed'},
            {'id': 'eee555fff6', 'guild': 10, 'author': int(OWNER), 'due': 1893456000, 'status': 'cancelled'},
        ]
        a = make_actions(entries=entries)
        out = await a.maybe_execute(make_message(), '[[do:reminder list]]')
        self.assertIn('aaa111bbb2', out)
        self.assertNotIn('ccc333ddd4', out)
        self.assertNotIn('eee555fff6', out)

    async def test_reminder_list_empty(self):
        a = make_actions()
        out = await a.maybe_execute(make_message(), '[[do:reminder list]]')
        self.assertIn('nahi hai', out)


class AnnounceTests(unittest.IsolatedAsyncioTestCase):
    async def test_announce_non_owner_rejected(self):
        a = make_actions()
        out = await a.maybe_execute(make_message(author_id=OTHER), '[[do:announce everyone | hello all]]')
        self.assertIn('owner', out)
        self.assertFalse(a.pending)

    async def test_announce_owner_gets_preview_not_post(self):
        a = make_actions()
        msg = make_message()
        out = await a.maybe_execute(msg, '[[do:announce everyone | tournament kal raat 9 baje]]')
        self.assertIn('preview', out)
        self.assertIn('abhi post NAHI hua', out)
        self.assertIn('#music', out)
        self.assertIn('tournament kal raat 9 baje', out)
        self.assertEqual(len(a.pending), 1)
        msg.channel.send.assert_not_called()

    async def test_announce_unknown_named_channel_clarifies(self):
        a = make_actions()
        msg = make_message()
        msg.guild.text_channels = [msg.channel]
        out = await a.maybe_execute(msg, '[[do:announce everyone #nope | hello all]]')
        self.assertIn('nahi mila', out)
        self.assertFalse(a.pending)

    async def test_announce_named_channel_posts_there(self):
        events = types.SimpleNamespace(
            id=21, name='events', send=AsyncMock(),
            permissions_for=lambda me: types.SimpleNamespace(mention_everyone=True))
        events.send.return_value = types.SimpleNamespace(
            jump_url='https://discord.com/channels/10/21/5')
        msg = make_message()
        msg.guild.text_channels = [msg.channel, events]
        a = make_actions()
        out = await a.maybe_execute(msg, '[[do:announce everyone #events | tournament kal raat 9 baje]]')
        self.assertIn('#events', out)
        ident = next(iter(a.pending))
        sent_channel[21] = events
        try:
            ok = await a.handle(msg, f'announce confirm {ident}')
        finally:
            sent_channel.pop(21, None)
        self.assertTrue(ok)
        events.send.assert_awaited_once()
        msg.channel.send.assert_not_called()

    async def test_announce_draft_mentions_stripped(self):
        a = make_actions()
        msg = make_message()
        out = await a.maybe_execute(msg, '[[do:announce everyone | hey <@12345> and @here listen]]')
        self.assertNotIn('<@12345>', out)
        self.assertNotIn('@here', out)

    async def test_announce_confirm_posts_and_reports_link(self):
        a = make_actions()
        msg = make_message()
        await a.maybe_execute(msg, '[[do:announce everyone | maintenance kal]]')
        ident = next(iter(a.pending))
        posted = types.SimpleNamespace(jump_url='https://discord.com/channels/10/20/30')
        msg.channel.send = AsyncMock(return_value=posted)
        sent_channel[20] = msg.channel
        consumed = await a.handle(msg, f'announce confirm {ident}')
        self.assertTrue(consumed)
        msg.channel.send.assert_awaited_once()
        args, kwargs = msg.channel.send.await_args
        self.assertTrue(args[0].startswith('@everyone'))
        self.assertIn('maintenance kal', args[0])
        self.assertTrue(kwargs['allowed_mentions'].everyone)
        self.assertFalse(kwargs['allowed_mentions'].users)
        self.assertFalse(kwargs['allowed_mentions'].roles)
        reply_text = msg.reply.await_args[0][0]
        self.assertIn('post ho gaya', reply_text)
        self.assertIn('https://discord.com/channels/10/20/30', reply_text)
        self.assertFalse(a.pending)  # single-use

    async def test_announce_confirm_wrong_user_rejected(self):
        a = make_actions()
        msg = make_message()
        await a.maybe_execute(msg, '[[do:announce everyone | maintenance kal]]')
        ident = next(iter(a.pending))
        other = make_message(author_id=OTHER)
        consumed = await a.handle(other, f'announce confirm {ident}')
        self.assertTrue(consumed)
        self.assertIn('kisi aur ka hai', other.reply.await_args[0][0])
        self.assertEqual(len(a.pending), 1)  # untouched
        msg.channel.send.assert_not_called()

    async def test_announce_confirm_wrong_channel_rejected(self):
        a = make_actions()
        msg = make_message()
        await a.maybe_execute(msg, '[[do:announce everyone | maintenance kal]]')
        ident = next(iter(a.pending))
        elsewhere = make_message(channel_id=99)
        await a.handle(elsewhere, f'announce confirm {ident}')
        self.assertIn('wahi channel', elsewhere.reply.await_args[0][0])
        self.assertEqual(len(a.pending), 1)

    async def test_announce_cancel_single_use(self):
        a = make_actions()
        msg = make_message()
        await a.maybe_execute(msg, '[[do:announce everyone | maintenance kal]]')
        ident = next(iter(a.pending))
        await a.handle(msg, f'announce cancel {ident}')
        self.assertIn('cancel kar diya', msg.reply.await_args[0][0])
        msg.channel.send.assert_not_called()
        msg2 = make_message()
        await a.handle(msg2, f'announce confirm {ident}')
        self.assertIn('nahi mila', msg2.reply.await_args[0][0])

    async def test_announce_expired_preview_rejected(self):
        a = make_actions()
        msg = make_message()
        await a.maybe_execute(msg, '[[do:announce everyone | maintenance kal]]')
        ident = next(iter(a.pending))
        a.pending[ident]['expires'] = time.monotonic() - 1
        await a.handle(msg, f'announce confirm {ident}')
        self.assertIn('expire', msg.reply.await_args[0][0])
        msg.channel.send.assert_not_called()

    async def test_announce_without_everyone_permission_refused(self):
        a = make_actions()
        msg = make_message()
        msg.channel.permissions_for = lambda me: types.SimpleNamespace(mention_everyone=False)
        out = await a.maybe_execute(msg, '[[do:announce everyone | maintenance kal]]')
        self.assertIn('permission nahi hai', out)
        self.assertFalse(a.pending)

    async def test_handle_ignores_unrelated_text(self):
        a = make_actions()
        self.assertFalse(await a.handle(make_message(), 'announce kar dena please'))

    async def test_announce_without_scope_asks_clarifying_question(self):
        a = make_actions()
        out = await a.maybe_execute(make_message(), '[[do:announce | kal tournament hai]]')
        self.assertIn('quiet', out)
        self.assertIn('everyone', out)
        self.assertFalse(a.pending)  # nothing prepared before scope is known

    async def test_announce_quiet_posts_without_ping(self):
        a = make_actions()
        msg = make_message()
        out = await a.maybe_execute(msg, '[[do:announce quiet | shanti se note]]')
        self.assertIn('none (quiet post)', out)
        ident = next(iter(a.pending))
        posted = types.SimpleNamespace(jump_url='https://discord.com/channels/10/20/31')
        msg.channel.send = AsyncMock(return_value=posted)
        sent_channel[20] = msg.channel
        await a.handle(msg, f'announce confirm {ident}')
        args, kwargs = msg.channel.send.await_args
        self.assertFalse(args[0].startswith('@everyone'))
        self.assertIn('shanti se note', args[0])
        self.assertFalse(kwargs['allowed_mentions'].everyone)

    async def test_announce_send_error_reports_unknown_no_retry(self):
        a = make_actions()
        msg = make_message()
        await a.maybe_execute(msg, '[[do:announce everyone | maintenance kal]]')
        ident = next(iter(a.pending))
        msg.channel.send = AsyncMock(side_effect=RuntimeError('gateway boom'))
        sent_channel[20] = msg.channel
        await a.handle(msg, f'announce confirm {ident}')
        reply_text = msg.reply.await_args[0][0]
        self.assertIn('confirm nahi hua', reply_text)  # unknown, not "failed"
        self.assertNotIn('post NAHI hua', reply_text)
        self.assertEqual(msg.channel.send.await_count, 1)  # no automatic duplicate

    async def test_music_queue_lists_tracks(self):
        player = types.SimpleNamespace(closed=False, current=track('Now'), paused=False,
                                       queue=[track('Q1'), track('Q2')], autoplay=False)
        a = make_actions(player=player)
        out = await a.maybe_execute(make_message(), '[[do:music queue]]')
        self.assertIn('1. Q1', out)
        self.assertIn('2. Q2', out)

    async def test_music_queue_empty(self):
        player = types.SimpleNamespace(closed=False, current=None, paused=False, queue=[], autoplay=False)
        a = make_actions(player=player)
        out = await a.maybe_execute(make_message(), '[[do:music queue]]')
        self.assertIn('khaali', out)

    async def test_pause_unverified_state_is_honest(self):
        async def pause_noop(paused):
            return None  # state never flips
        player = types.SimpleNamespace(closed=False, current=track('S'), paused=False, queue=[],
                                       autoplay=False, pause=AsyncMock(side_effect=pause_noop))
        a = make_actions(player=player)
        out = await a.maybe_execute(make_message(), '[[do:music pause]]')
        self.assertIn('confirm nahi hui', out)
        self.assertNotIn('pause kar diya', out)

    async def test_stop_unverified_state_is_honest(self):
        async def stop_noop(clear=False):
            return None  # state never changes
        player = types.SimpleNamespace(closed=False, current=track('S'), paused=False,
                                       queue=[track('Q')], autoplay=False, interrupt=AsyncMock(side_effect=stop_noop))
        a = make_actions(player=player)
        out = await a.maybe_execute(make_message(), '[[do:music stop]]')
        self.assertIn('confirm nahi hui', out)
        self.assertNotIn('band kar diya', out)


if __name__ == '__main__':
    unittest.main()
