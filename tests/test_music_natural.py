import asyncio
import unittest
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import discord
from music import natural
from music.integration import Music
from music.player import MusicError

class Parse(unittest.TestCase):
    def test_screenshot_phrase_and_variants(self):
        cases={'Is song ke Baad masquerade of the guilty bajao':'masquerade of the guilty','masquerade of the guilty bajao':'masquerade of the guilty',
               'jeene laga hoon play kar do':'jeene laga hoon','play tum hi ho':'tum hi ho','Faded alan walker laga do':'Faded alan walker',
               'https://youtu.be/abc123 bajao':'https://youtu.be/abc123','ok bhai kesariya chala do na':'kesariya',
               'Agar Tum Saath Ho bajao':'Agar Tum Saath Ho',"Don't Stop Me Now bajao":"Don't Stop Me Now",'Nahi Saamne Tu bajao':'Nahi Saamne Tu',
               'If I Ain\'t Got You play kar do':"If I Ain't Got You",'Who Says? bajao':'Who Says?','Mat Ja Mat Ja laga do':'Mat Ja Mat Ja'}
        for text,query in cases.items():self.assertEqual(natural.parse(text),query,text)
    def test_not_play_requests(self):
        for text in ('tum kaisi ho','bajao','koi song bajao?','mat bajao','hello\nbajao','Faded mat bajao','gaana mat bajao','don\'t bajao','nahi bajao','Junaid ne bola faded bajao','agar faded mile toh bajao','"faded bajao"','> faded bajao','`faded bajao`','faded bajao Kristy','kristy faded bajao kristy','a '*200+'bajao','<@5> bajao'):
            self.assertIsNone(natural.parse(text),text)
    def test_no_title(self):
        for text in ('gaana bajao','ye gaana bajao','koi song laga do'):self.assertEqual(natural.parse(text),'',text)

class Natural(unittest.IsolatedAsyncioTestCase):
    def build(self,playing=False,channel_type=discord.ChannelType.voice):
        track=S(title='Masquerade of the Guilty')
        player=S(current=object() if playing else None,queue=[],closed=False,channel=S(id=7),add=AsyncMock())
        manager=S(media=S(search=AsyncMock(return_value=[track,S(title='other')])),get=AsyncMock(return_value=player),panel=AsyncMock(),
                  caller_channel=lambda u,c:S(id=7))
        m=Music.__new__(Music);m.manager=manager;m.busy=set()
        msg=S(channel=S(id=7,type=channel_type),guild=S(id=1),author=S(id=2),reply=AsyncMock())
        return m,msg,player,manager,track
    async def test_idle_plays_first_result_via_add(self):
        m,msg,p,mg,t=self.build()
        self.assertTrue(await m.natural(msg,'Is song ke baad masquerade of the guilty bajao'))
        mg.media.search.assert_awaited_once_with('masquerade of the guilty',2)
        p.add.assert_awaited_once_with(t);self.assertTrue(msg.reply.call_args.args[0].startswith('Laga rahi'))
        self.assertTrue(mg.get.call_args.kwargs['join'])
    async def test_busy_queues_behind_current_without_interrupt(self):
        m,msg,p,mg,t=self.build(playing=True);p.interrupt=AsyncMock();p.pause=AsyncMock()
        await m.natural(msg,'faded bajao')
        p.add.assert_awaited_once_with(t);p.interrupt.assert_not_awaited();p.pause.assert_not_awaited()
        self.assertTrue(msg.reply.call_args.args[0].startswith('Queue mein'))
    async def test_not_vc_chat_or_not_play_falls_through(self):
        m,msg,p,mg,t=self.build(channel_type=discord.ChannelType.text)
        self.assertFalse(await m.natural(msg,'faded bajao'));mg.media.search.assert_not_awaited()
        m,msg,p,mg,t=self.build();self.assertFalse(await m.natural(msg,'tum kaisi ho'));self.assertFalse(await m.natural(msg,None))
    async def test_member_not_in_that_vc_is_refused_before_search(self):
        m,msg,p,mg,t=self.build()
        def deny(u,c):raise MusicError('Join an ordinary voice channel first')
        mg.caller_channel=deny
        self.assertTrue(await m.natural(msg,'faded bajao'))
        mg.media.search.assert_not_awaited();p.add.assert_not_awaited();self.assertIn('Join',msg.reply.call_args.args[0])
    async def test_no_title_asks_and_searches_nothing(self):
        m,msg,p,mg,t=self.build();await m.natural(msg,'gaana bajao')
        mg.media.search.assert_not_awaited();self.assertIn('Kaunsa',msg.reply.call_args.args[0])
    async def test_search_failure_replies_without_secrets_and_clears_busy(self):
        m,msg,p,mg,t=self.build();mg.media.search.side_effect=RuntimeError('boom key=abc')
        await m.natural(msg,'faded bajao')
        self.assertNotIn('abc',msg.reply.call_args.args[0]);self.assertEqual(m.busy,set());p.add.assert_not_awaited()
    async def test_mentions_suppressed_in_reply(self):
        m,msg,p,mg,t=self.build();await m.natural(msg,'faded bajao')
        self.assertFalse(msg.reply.call_args.kwargs['allowed_mentions'].everyone)

    async def test_forward_webhook_bot_author_ignored(self):
        for attr in (dict(message_snapshots=[object()]),dict(webhook_id=5)):
            m,msg,p,mg,t=self.build();msg.__dict__.update(attr)
            self.assertFalse(await m.natural(msg,'faded bajao'));mg.media.search.assert_not_awaited()
        m,msg,p,mg,t=self.build();msg.author.bot=True
        self.assertFalse(await m.natural(msg,'faded bajao'))
    async def test_title_words_reach_search_unchanged(self):
        m,msg,p,mg,t=self.build();await m.natural(msg,"Don't Stop Me Now bajao")
        mg.media.search.assert_awaited_once_with("Don't Stop Me Now",2)

class RealPlayerRace(unittest.IsolatedAsyncioTestCase):
    async def test_two_members_idle_add_starts_single_run_task(self):
        import types
        from music.player import Manager, GuildPlayer
        from music.media import Track
        mgr=Manager(types.SimpleNamespace());mgr.note=AsyncMock();mgr.panel=AsyncMock()
        c=types.SimpleNamespace(id=20,guild=types.SimpleNamespace(id=10),send=AsyncMock(),members=[])
        p=GuildPlayer(mgr,c);started=[]
        async def fake_run():
            started.append(1);await asyncio.sleep(0.05)
        p.run=fake_run
        try:
            await asyncio.gather(p.add(Track('jNQXAC9IVRw','a',19,1)),p.add(Track('jNQXAC9IVRw','b',19,2)))
            await asyncio.sleep(0.01)
            self.assertEqual(started,[1]);self.assertEqual(len(p.queue),2)
        finally:
            if p.task:await asyncio.gather(p.task,return_exceptions=True)
            p.temp.cleanup()
