import asyncio
import datetime as dt
import pathlib
import tempfile
from types import SimpleNamespace as S
import unittest
from unittest.mock import AsyncMock,patch
import discord
import timed_messages as tm
import schedule_commands as sc

class Schedules(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=tm.Store(pathlib.Path(self.tmp.name)/'queue.json')
        self.author=S(id=1,bot=False);self.target=S(id=2,bot=False)
        self.guild=S(id=10,unavailable=False,me=S(id=99))
        self.members={1:self.author,2:self.target,99:self.guild.me};self.guild.get_member=self.members.get
        self.dest=S(id=20,guild=self.guild,type=discord.ChannelType.text,name='gaming',send=AsyncMock(return_value=S(id=77)))
        self.dest.permissions_for=lambda m:S(view_channel=True,send_messages=True)
        self.guild.get_channel=lambda cid:self.dest if cid==20 else None
        self.client=S(is_ready=lambda:True,get_guild=lambda gid:self.guild if gid==10 else None)
        self.worker=tm.Worker(self.client,self.store)
    def create(self,**kw):
        data=dict(guild=10,author=1,source=20,dest=20,targets=[2],body='<@2> hello',due=100,now=0);data.update(kw)
        return self.store.create(**data)
    def arm(self):
        e=self.create();self.store.control(e['id'],10,1,'confirm',1);return e
    async def test_preview_never_fires(self):
        self.create();await self.worker.tick(100);self.dest.send.assert_not_awaited()
    async def test_confirm_and_exact_due_once(self):
        self.arm();await self.worker.tick(99);self.dest.send.assert_not_awaited()
        await self.worker.tick(100);await self.worker.tick(100)
        self.dest.send.assert_awaited_once();self.assertEqual(self.store.load()[0]['sent_message'],77)
        allowed=self.dest.send.call_args.kwargs['allowed_mentions'];self.assertFalse(allowed.everyone);self.assertFalse(allowed.roles)
    async def test_late_window_and_missed(self):
        self.arm();await self.worker.tick(221);self.dest.send.assert_not_awaited();self.assertEqual(self.store.load()[0]['status'],'missed')
    async def test_unknown_send_never_replayed(self):
        self.arm();self.dest.send.side_effect=asyncio.TimeoutError()
        await self.worker.tick(100);await self.worker.tick(110);self.dest.send.assert_awaited_once();self.assertEqual(self.store.load()[0]['status'],'uncertain')
    async def test_revoked_permissions_and_member_departure(self):
        self.arm();self.dest.permissions_for=lambda m:S(view_channel=m.id!=1,send_messages=True)
        await self.worker.tick(100);self.dest.send.assert_not_awaited()
        self.store.path.unlink();self.arm();del self.members[2];await self.worker.tick(100);self.dest.send.assert_not_awaited()
        self.store.path.unlink();self.members[2]=self.target;self.arm();del self.members[1];await self.worker.tick(100);self.dest.send.assert_not_awaited()
    async def test_checkpoint_write_failure_sends_nothing(self):
        self.arm()
        original=self.store.save;calls=0
        def save(data):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError()
            original(data)
        with patch.object(self.store,'save',side_effect=save):
            with self.assertRaises(OSError):await self.worker.tick(100)
        self.dest.send.assert_not_awaited()
    async def test_final_write_failure_durable_dispatching(self):
        self.arm();original=self.store.save
        def save(data):
            if any(e['status']=='sent' for e in data):raise OSError()
            original(data)
        with patch.object(self.store,'save',side_effect=save):
            with self.assertRaises(OSError):await self.worker.tick(100)
        self.assertEqual(self.store.load()[0]['status'],'dispatching');self.assertTrue(self.worker.halted)
    async def test_concurrent_create_during_send_not_lost(self):
        self.arm()
        async def send(*a,**kw):self.create(due=200);return S(id=77)
        self.dest.send.side_effect=send;await self.worker.tick(100)
        self.assertEqual(len(self.store.load()),2)
    async def test_cancel_and_cross_user_server_controls(self):
        e=self.create()
        for guild,user in ((11,1),(10,2)):
            with self.assertRaises(ValueError):self.store.control(e['id'],guild,user,'confirm',1)
        self.store.control(e['id'],10,2,'cancel',1,True)
        with self.assertRaises(ValueError):self.store.control(e['id'],10,1,'confirm',1)
    async def test_expired_preview(self):
        e=self.create()
        with self.assertRaises(ValueError):self.store.control(e['id'],10,1,'confirm',121)
    async def test_corrupt_state_and_atomic_write(self):
        self.store.path.write_text('{oops')
        with self.assertRaises(ValueError):self.create()
        self.assertEqual(self.store.path.read_text(),'{oops')
        self.store.path.unlink()
        with patch('timed_messages.os.replace',side_effect=OSError):
            with self.assertRaises(OSError):self.create()
        self.assertEqual(self.store.load(),[])
    async def test_active_cap(self):
        for _ in range(10):self.create()
        with self.assertRaises(ValueError):self.create()
    async def test_disconnect_flag_even_if_client_ready(self):
        self.arm();self.worker.connected=lambda:False;await self.worker.tick(100);self.dest.send.assert_not_awaited()
    async def test_dispatching_recovered_uncertain_before_loop(self):
        e=self.arm();data=self.store.load();data[0]['status']='dispatching';self.store.save(data)
        with patch.object(self.worker,'tick',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await self.worker.run()
        self.assertEqual(self.store.load()[0]['status'],'uncertain');self.dest.send.assert_not_awaited()
    async def test_not_ready_waits_until_window_expires(self):
        self.arm();self.client.is_ready=lambda:False
        await self.worker.tick(100);self.dest.send.assert_not_awaited()
        await self.worker.tick(221);self.assertEqual(self.store.load()[0]['status'],'missed')
    async def test_command_preview_confirm_order_and_exact_payload(self):
        commands=sc.Commands(self.store,lambda g,n:(self.target,False))
        msg=S(guild=self.guild,author=self.author,channel=self.dest,created_at=dt.datetime.now(dt.timezone.utc),reply=AsyncMock())
        guard=lambda *a,**kw:'ok'
        await commands.handle(msg,'schedule 10 minute baad | here | Mayank | hello, exact words',self.client,guard)
        e=self.store.load()[0];self.assertEqual(e['status'],'preview');self.assertEqual(e['body'],'<@2>\nhello, exact words')
        await commands.handle(msg,'schedule confirm '+e['id'],self.client,guard)
        self.assertEqual(self.store.load()[0]['status'],'scheduled');self.dest.send.assert_not_awaited()
    async def test_payload_mentions_rejected(self):
        commands=sc.Commands(self.store,lambda g,n:(self.target,False))
        msg=S(guild=self.guild,author=self.author,channel=self.dest,created_at=dt.datetime.now(dt.timezone.utc),reply=AsyncMock())
        await commands.handle(msg,'schedule 10 minute baad | here | Mayank | @everyone',self.client,lambda *a,**kw:'ok')
        self.assertEqual(self.store.load(),[])

class Dates(unittest.TestCase):
    def setUp(self):self.now=dt.datetime(2026,11,1,19,0,tzinfo=tm.IST)
    def test_exact_and_relative(self):
        for text,expected in [('10 minute baad','2026-11-01T19:10:00+05:30'),('kal 8 AM','2026-11-02T08:00:00+05:30'),('2026-11-03 20:30','2026-11-03T20:30:00+05:30')]:
            self.assertEqual(dt.datetime.fromtimestamp(tm.parse_time(text,self.now),tm.IST).isoformat(),expected)
        self.assertIn('Monday, 02 November 2026',tm.shown(tm.parse_time('kal 8 AM',self.now)))
    def test_ambiguous_past_and_midnight(self):
        for text in ('8 baje','kal 8','aaj 8 AM','2026-11-03 25:00','2026-11-03 8'):
            with self.assertRaises(ValueError):tm.parse_time(text,self.now)
        with self.assertRaises(ValueError):tm.parse_time('kal 8 AM',self.now.replace(hour=1))

class MoreSchedules(Schedules):
    async def test_cancel_different_due_job_during_first_send(self):
        first=self.arm();second=self.create();self.store.control(second['id'],10,1,'confirm',1)
        async def send(*args,**kw):self.store.control(second['id'],10,1,'cancel',100);return S(id=77)
        self.dest.send.side_effect=send
        await self.worker.tick(100);await self.worker.tick(100)
        self.dest.send.assert_awaited_once();self.assertEqual(self.store.load()[1]['status'],'cancelled')
    async def test_cancel_dispatching_cannot_rearm_or_claim_cancelled(self):
        first=self.arm()
        async def send(*args,**kw):
            with self.assertRaises(ValueError):self.store.control(first['id'],10,1,'cancel',100)
            return S(id=77)
        self.dest.send.side_effect=send;await self.worker.tick(100)
        self.assertEqual(self.store.load()[0]['status'],'sent')
    async def test_native_target_order_and_partial_resolution(self):
        third=S(id=3,bot=False);self.members[3]=third
        commands=sc.Commands(self.store,lambda g,n:(None,True))
        msg=S(guild=self.guild,author=self.author,channel=self.dest,created_at=dt.datetime.now(dt.timezone.utc),reply=AsyncMock())
        await commands.handle(msg,'schedule 10 minute baad | <#20> | <@3>, <@2>, <@3> | exact words',self.client,lambda *a,**kw:'ok')
        self.assertEqual(self.store.load()[0]['targets'],[3,2])
        self.assertEqual(self.store.load()[0]['body'],'<@3> <@2>\nexact words')
        await commands.handle(msg,'schedule 10 minute baad | <#20> | <@2>, <@404> | no partial job',self.client,lambda *a,**kw:'ok')
        self.assertEqual(len(self.store.load()),1)
    async def test_corrupt_values_fail_closed(self):
        self.create();data=self.store.load();data[0]['due']='bad';self.store.save(data)
        with self.assertRaises(ValueError):await self.worker.tick(100)
        self.dest.send.assert_not_awaited()
