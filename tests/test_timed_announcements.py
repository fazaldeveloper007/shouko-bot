import datetime as dt
import pathlib
import tempfile
import unittest
from types import SimpleNamespace as S
from unittest.mock import AsyncMock,patch
import discord
import actions
import timed_messages as tm

A=dt.datetime(2026,11,24,20,0,tzinfo=tm.IST)
class TimedAnnouncements(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=tm.Store(pathlib.Path(self.tmp.name)/'s.json')
        self.author=S(id=1,bot=False);self.me=S(id=99)
        self.guild=S(id=10,me=self.me,unavailable=False)
        self.permissions=S(view_channel=True,send_messages=True,mention_everyone=True)
        self.ch=S(id=20,name='events',guild=self.guild,type=discord.ChannelType.text,
            send=AsyncMock(return_value=S(id=777,jump_url='https://discord.com/channels/10/20/777')),
            permissions_for=lambda m:self.permissions)
        self.guild.text_channels=[self.ch];self.guild.get_channel=lambda i:self.ch if i==20 else None
        self.guild.get_member=lambda i:self.author if i==1 else None
        self.client=S(get_channel=lambda i:self.ch if i==20 else None,get_guild=lambda i:self.guild if i==10 else None,is_ready=lambda:True)
        self.m=S(author=self.author,channel=self.ch,guild=self.guild,created_at=A,
            content='Kristy schedule announcement tomorrow 9 PM everyone in #events',reply=AsyncMock(),reference=None)
        self.a=actions.Actions(self.client,'1',self.store)
        self.worker=tm.Worker(self.client,self.store)
    async def preview(self,scope='everyone',when='tomorrow 9 PM'):
        with patch('actions.time.time',return_value=A.timestamp()):
            return await self.a.maybe_execute(self.m,f'[[do:announce {scope} #events | {when} | Tournament starts soon!]]')
    async def confirm(self):
        with patch('actions.time.time',return_value=A.timestamp()+10):await self.a.handle(self.m,'confirm')
    async def test_preview_confirm_persists_then_send_once_no_ai(self):
        out=await self.preview();self.assertIn('25 November',out);self.assertIn('09:00',out)
        self.assertEqual(self.store.load(),[]);self.ch.send.assert_not_awaited()
        await self.confirm();e=self.store.load()[0]
        self.assertEqual((e['kind'],e['targets'],e['ping']),('announcement',[],True))
        await tm.Worker(self.client,tm.Store(self.store.path)).tick(e['due'])
        await self.worker.tick(e['due']+10)
        self.ch.send.assert_awaited_once()
        mentions=self.ch.send.call_args.kwargs['allowed_mentions']
        self.assertTrue(mentions.everyone);self.assertFalse(mentions.users);self.assertFalse(mentions.roles)
        self.assertEqual(self.store.load()[0]['status'],'sent')
    async def test_quiet_never_pings(self):
        await self.preview('quiet');await self.confirm();e=self.store.load()[0]
        await self.worker.tick(e['due'])
        self.assertNotIn('@everyone',e['body']);self.assertFalse(self.ch.send.call_args.kwargs['allowed_mentions'].everyone)
    async def test_wrong_user_cannot_confirm(self):
        await self.preview();self.m.author=S(id=2)
        await self.confirm();self.assertEqual(self.store.load(),[])
    async def test_wrong_channel_cannot_confirm(self):
        await self.preview();self.m.channel=S(id=30)
        await self.confirm();self.assertEqual(self.store.load(),[])
    async def test_cancel_does_not_save(self):
        await self.preview();await self.a.handle(self.m,'cancel');self.assertEqual(self.store.load(),[])
    async def test_time_not_in_human_request_never_saved(self):
        out=await self.preview(when='tomorrow 8 PM');self.assertIn('verify nahi',out);self.assertFalse(self.a.pending)
    async def test_dropped_posting_time_never_posts_immediately(self):
        out=await self.a.maybe_execute(self.m,'[[do:announce everyone | Tournament starts soon!]]')
        self.assertIn('Posting time',out);self.assertFalse(self.a.pending)
    async def test_permission_loss_at_confirm_refuses_save(self):
        await self.preview();self.permissions.mention_everyone=False
        await self.confirm();self.assertEqual(self.store.load(),[])
    async def test_permission_loss_at_send_stops_without_ping(self):
        await self.preview();await self.confirm();e=self.store.load()[0];self.permissions.mention_everyone=False
        await self.worker.tick(e['due']);self.ch.send.assert_not_awaited();self.assertEqual(self.store.load()[0]['status'],'failed')
    async def test_late_skip_keeps_policy(self):
        await self.preview();await self.confirm();e=self.store.load()[0]
        await self.worker.tick(e['due']+121);self.assertEqual(self.store.load()[0]['status'],'missed');self.ch.send.assert_not_awaited()
    async def test_uncertain_send_never_replays(self):
        await self.preview();await self.confirm();e=self.store.load()[0];self.ch.send.side_effect=TimeoutError
        await self.worker.tick(e['due']);await self.worker.tick(e['due']+1)
        self.assertEqual(self.store.load()[0]['status'],'uncertain');self.ch.send.assert_awaited_once()
    async def test_existing_cancel_controls_work(self):
        await self.preview();await self.confirm();e=self.store.load()[0]
        self.store.control(e['id'],10,1,'cancel',A.timestamp()+20)
        await self.worker.tick(e['due']);self.ch.send.assert_not_awaited()
    async def test_expired_time_at_confirm_is_not_saved(self):
        await self.preview()
        with patch('actions.time.time',return_value=A.timestamp()+2*86400):await self.a.handle(self.m,'confirm')
        self.assertEqual(self.store.load(),[])
    async def test_storage_failure_does_not_claim_saved(self):
        await self.preview()
        with patch.object(self.store,'save',side_effect=OSError):await self.confirm()
        self.assertIn('save nahi hua',self.m.reply.call_args.args[0]);self.ch.send.assert_not_awaited()
    async def test_explicit_syntax_preview_only(self):
        with patch('actions.time.time',return_value=A.timestamp()):
            consumed=await self.a.handle(self.m,'announce schedule tomorrow 9 PM | quiet | #events | Tournament starts soon!')
        self.assertTrue(consumed);self.assertEqual(len(self.a.pending),1);self.assertEqual(self.store.load(),[])
    async def test_non_owner_is_refused(self):
        self.m.author=S(id=2);out=await self.preview();self.assertIn('owner',out);self.assertFalse(self.a.pending)
    async def test_bad_record_cannot_enable_unapproved_mentions(self):
        await self.preview();await self.confirm();data=self.store.load();data[0]['targets']=[1];self.store.save(data)
        with self.assertRaises(ValueError):self.store.load()

    async def test_channel_clarification_keeps_selected_time(self):
        self.m.content='schedule announcement tomorrow 9 PM in #missing'
        with patch('actions.time.time',return_value=A.timestamp()):
            out=await self.a.maybe_execute(self.m,'[[do:announce quiet #missing | tomorrow 9 PM | Tournament starts soon!]]')
        self.assertIn('nahi mila',out)
        self.m.content='isi channel mein dedo'
        out=await self.a.maybe_execute(self.m,'[[do:announce]]')
        self.assertIn('25 November',out)
        self.assertIsNotNone(next(iter(self.a.pending.values()))['due'])
