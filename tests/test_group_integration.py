import asyncio
import os
import types
import unittest
from unittest.mock import AsyncMock, patch
import discord
from music.player import Manager, MusicError
from music.integration import Music
from voice_live.group_integration import LiveGroups
from voice_live.group_policy import GroupPolicyError
from test_group_policy import A, B, BOT, G, CH


class FakeVC:
    def __init__(self):
        self.id=int(CH)
        self.guild=types.SimpleNamespace(id=int(G),voice_client=None,
            me=types.SimpleNamespace(voice=None))
    def permissions_for(self, member):
        return types.SimpleNamespace(view_channel=True, connect=True, speak=True,send_messages=True)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client=types.SimpleNamespace(user=types.SimpleNamespace(id=int(BOT)))
        self.m=Manager(self.client)
        self.live=LiveGroups(self.client,self.m,'PRIVATE',notice_approved=True,confirmed_free_tier=True)
        self.vc=FakeVC()
        self.i=types.SimpleNamespace(user=types.SimpleNamespace(id=int(A),voice=types.SimpleNamespace(channel=self.vc)),
            guild_id=int(G),channel=self.vc)
        self.data=dict(guild_id=G, owner_id=A,notice_revision='group-free-future-v2')

    async def test_live_reservation_blocks_music_and_natural_get_before_connection(self):
        self.m.voice_reservations[int(G)]=object()
        with patch('music.player.discord.VoiceChannel',FakeVC):
            with self.assertRaisesRegex(MusicError,'Live voice'):
                await self.m.get(self.i.user,self.vc,join=True)
        self.assertFalse(self.m.players)

    async def test_live_rejects_existing_music_without_stealing_connection(self):
        self.m.players[int(G)]=types.SimpleNamespace(closed=False)
        with patch('voice_live.group_integration.load_config',return_value=(self.data,(A,B))),patch('voice_live.group_integration.discord.VoiceChannel',FakeVC),patch('voice_live.group_integration.discord_snapshot'):
            with self.assertRaisesRegex(GroupPolicyError,'BUSY'):
                await self.live.start(self.i)
        self.assertFalse(self.live.sessions)

    async def test_double_live_start_only_one_reservation(self):
        hold=asyncio.Event()
        async def wait(*args):
            await hold.wait()
        with patch('voice_live.group_integration.load_config',return_value=(self.data,(A,B))),patch('voice_live.group_integration.discord.VoiceChannel',FakeVC),patch('voice_live.group_integration.discord_snapshot'),patch('voice_live.group_integration.answer',new=AsyncMock()),patch.object(self.live,'_run',side_effect=wait):
            await self.live.start(self.i)
            with self.assertRaisesRegex(GroupPolicyError,'BUSY'):
                await self.live.start(self.i)
            hold.set()
            await self.live.sessions[int(G)].task
        self.assertEqual(len(self.m.voice_reservations),1)

    async def test_leave_live_waits_for_cleanup_same_session(self):
        stopped=asyncio.Event()
        session=types.SimpleNamespace(channel=self.vc,stopped=stopped,coordinator=None,
            task=None,leave_confirmed=False)
        async def finish():
            await stopped.wait()
            session.leave_confirmed=True
        session.task=asyncio.create_task(finish())
        self.live.sessions[int(G)]=session
        with patch.object(self.live,'_allowed_ids',return_value=(A,B)),patch('voice_live.group_integration.answer',new=AsyncMock()) as answer:
            self.assertTrue(await self.live.leave(self.i))
        self.assertTrue(session.task.done())
        self.assertTrue(session.leave_confirmed)
        self.assertEqual(answer.await_count,1)

    async def test_leave_from_other_vc_cannot_stop(self):
        self.live.sessions[int(G)]=types.SimpleNamespace(channel=types.SimpleNamespace(id=999))
        with self.assertRaises(MusicError):
            await self.live.leave(self.i)

    async def test_missing_key_preconnect_releases_reservation(self):
        session=types.SimpleNamespace(channel=self.vc,stopped=asyncio.Event(),task=None,
            coordinator=None,view=None,message=None,leave_confirmed=True)
        self.m.voice_reservations[int(G)]=session
        self.live.sessions[int(G)]=session
        with patch.dict(os.environ,{'GEMINI_API_KEY':''}):
            await self.live._run(session,None)
        self.assertFalse(self.m.voice_reservations)
        self.assertFalse(self.live.sessions)

    async def test_unconfirmed_leave_retains_reservation(self):
        session=types.SimpleNamespace(channel=self.vc,stopped=asyncio.Event(),task=None,
            coordinator=None,view=None,message=None,leave_confirmed=False)
        self.m.voice_reservations[int(G)]=session
        self.live.sessions[int(G)]=session
        with patch.dict(os.environ,{'GEMINI_API_KEY':'FAKE'}),patch('voice_live.group_integration.run_approved_group',new=AsyncMock()):
            await self.live._run(session,None)
        self.assertIn(int(G),self.m.voice_reservations)
        self.assertFalse(self.live.sessions)

    async def test_music_dispatch_leave_live_first_not_get_music_player(self):
        obj=object.__new__(Music)
        obj.live=types.SimpleNamespace(leave=AsyncMock(return_value=True))
        obj.manager=types.SimpleNamespace(get=AsyncMock(),caller_channel=lambda *a:None)
        await obj.execute(self.i,'leave')
        obj.live.leave.assert_awaited_once()
        obj.manager.get.assert_not_awaited()

    async def test_second_configured_guild_can_start_live(self):
        G2='7'*18
        vc2=FakeVC()
        vc2.guild=types.SimpleNamespace(id=int(G2),voice_client=None,me=types.SimpleNamespace(voice=None))
        i2=types.SimpleNamespace(user=types.SimpleNamespace(id=int(A),voice=types.SimpleNamespace(channel=vc2)),
            guild_id=int(G2),channel=vc2)
        data=dict(self.data,guild_id=[G,G2])
        hold=asyncio.Event()
        seen=[]
        async def wait(session,policy):
            seen.append(policy.scope.guild)
            await hold.wait()
        with patch('voice_live.group_integration.load_config',return_value=(data,(A,B))),patch('voice_live.group_integration.discord.VoiceChannel',FakeVC),patch('voice_live.group_integration.discord_snapshot'),patch('voice_live.group_integration.answer',new=AsyncMock()),patch.object(self.live,'_run',side_effect=wait):
            await self.live.start(i2)
            self.assertIn(int(G2),self.m.voice_reservations)
            hold.set()
            await self.live.sessions[int(G2)].task
        self.assertEqual(seen,[G2])

    async def test_unlisted_guild_rejected_with_list_config(self):
        G2='7'*18
        vc2=FakeVC()
        vc2.guild=types.SimpleNamespace(id=int(G2),voice_client=None,me=types.SimpleNamespace(voice=None))
        i2=types.SimpleNamespace(user=types.SimpleNamespace(id=int(A),voice=types.SimpleNamespace(channel=vc2)),
            guild_id=int(G2),channel=vc2)
        with patch('voice_live.group_integration.load_config',return_value=(dict(self.data,guild_id=[G]),(A,B))),patch('voice_live.group_integration.discord.VoiceChannel',FakeVC):
            with self.assertRaisesRegex(GroupPolicyError,'GROUP_GUILD_NOT_ALLOWED'):
                await self.live.start(i2)
        self.assertFalse(self.live.sessions)
        self.assertFalse(self.m.voice_reservations)

    async def test_voice_channel_in_other_guild_than_command_rejected(self):
        G2='7'*18
        vc2=FakeVC()
        vc2.guild=types.SimpleNamespace(id=int(G2),voice_client=None,me=types.SimpleNamespace(voice=None))
        i2=types.SimpleNamespace(user=types.SimpleNamespace(id=int(A),voice=types.SimpleNamespace(channel=vc2)),
            guild_id=int(G),channel=vc2)
        with patch('voice_live.group_integration.load_config',return_value=(dict(self.data,guild_id=[G,G2]),(A,B))),patch('voice_live.group_integration.discord.VoiceChannel',FakeVC):
            with self.assertRaisesRegex(GroupPolicyError,'GROUP_GUILD_NOT_ALLOWED'):
                await self.live.start(i2)
        self.assertFalse(self.live.sessions)


class EndLineTests(unittest.TestCase):
    def test_fixed_codes_pass_and_anything_else_is_dropped(self):
        from voice_live.group_bridge import end_line
        self.assertEqual(end_line({'code': 'IDLE_LIMIT'}), 'LIVE_ENDED: IDLE_LIMIT')
        self.assertEqual(end_line({'code': 'RECEIVE_ERROR', 'receiveStage': 'STREAM_OTHER',
            'errorClass': 'OTHER'}), 'LIVE_ENDED: RECEIVE_ERROR/STREAM_OTHER/OTHER')
        for bad in ('secret message', 'lowercase', '', 'IDLE_LIMIT; rm -rf ~', None, 123):
            self.assertEqual(end_line({'code': bad}), 'LIVE_ENDED: UNKNOWN')
