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
        player = types.SimpleNamespace(closed=False, current=track('S'), paused=False, queue=[], autoplay=False,
                                       pause=AsyncMock())
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

    async def test_music_stop_clears(self):
        player = types.SimpleNamespace(closed=False, current=track('S'), paused=False,
                                       queue=[track('Q')], autoplay=False, interrupt=AsyncMock())
        a = make_actions(player=player)
        out = await a.maybe_execute(make_message(), '[[do:music stop]]')
        player.interrupt.assert_awaited_once_with(clear=True)
        self.assertIn('queue clear', out)

    async def test_reminder_list_own_only(self):
        entries = [
            {'id': 'aaa111bbb2', 'guild': 10, 'author': int(OWNER), 'due': 1893456000, 'status': 'confirmed'},
            {'id': 'ccc333ddd4', 'guild': 10, 'author': int(OTHER), 'due': 1893456000, 'status': 'confirmed'},
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
        out = await a.maybe_execute(make_message(author_id=OTHER), '[[do:announce | hello all]]')
        self.assertIn('owner', out)
        self.assertFalse(a.pending)

    async def test_announce_owner_gets_preview_not_post(self):
        a = make_actions()
        msg = make_message()
        out = await a.maybe_execute(msg, '[[do:announce | tournament kal raat 9 baje]]')
        self.assertIn('preview', out)
        self.assertIn('abhi post NAHI hua', out)
        self.assertIn('#music', out)
        self.assertIn('tournament kal raat 9 baje', out)
        self.assertEqual(len(a.pending), 1)
        msg.channel.send.assert_not_called()

    async def test_announce_draft_mentions_stripped(self):
        a = make_actions()
        msg = make_message()
        out = await a.maybe_execute(msg, '[[do:announce | hey <@12345> and @here listen]]')
        self.assertNotIn('<@12345>', out)
        self.assertNotIn('@here', out)

    async def test_announce_confirm_posts_and_reports_link(self):
        a = make_actions()
        msg = make_message()
        await a.maybe_execute(msg, '[[do:announce | maintenance kal]]')
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
        await a.maybe_execute(msg, '[[do:announce | maintenance kal]]')
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
        await a.maybe_execute(msg, '[[do:announce | maintenance kal]]')
        ident = next(iter(a.pending))
        elsewhere = make_message(channel_id=99)
        await a.handle(elsewhere, f'announce confirm {ident}')
        self.assertIn('wahi channel', elsewhere.reply.await_args[0][0])
        self.assertEqual(len(a.pending), 1)

    async def test_announce_cancel_single_use(self):
        a = make_actions()
        msg = make_message()
        await a.maybe_execute(msg, '[[do:announce | maintenance kal]]')
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
        await a.maybe_execute(msg, '[[do:announce | maintenance kal]]')
        ident = next(iter(a.pending))
        a.pending[ident]['expires'] = time.monotonic() - 1
        await a.handle(msg, f'announce confirm {ident}')
        self.assertIn('expire', msg.reply.await_args[0][0])
        msg.channel.send.assert_not_called()

    async def test_announce_without_everyone_permission_refused(self):
        a = make_actions()
        msg = make_message()
        msg.channel.permissions_for = lambda me: types.SimpleNamespace(mention_everyone=False)
        out = await a.maybe_execute(msg, '[[do:announce | maintenance kal]]')
        self.assertIn('permission nahi hai', out)
        self.assertFalse(a.pending)

    async def test_handle_ignores_unrelated_text(self):
        a = make_actions()
        self.assertFalse(await a.handle(make_message(), 'announce kar dena please'))


if __name__ == '__main__':
    unittest.main()
