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

class RepairTests(Schedules):
    async def test_natural_intents_no_false_ok_no_saved_job(self):
        commands=sc.Commands(self.store,lambda *a:(self.target,False))
        msg=S(guild=self.guild,author=self.author,channel=self.dest,created_at=dt.datetime.now(dt.timezone.utc),reply=AsyncMock(return_value=S(id=700)),reference=None)
        for text in ('reminder laga do 9:20 PM ka','10 min baad Mayank ko isi channel mein bula dena','9:20 PM pe message bhejna'):
            self.assertTrue(await commands.handle(msg,text,self.client,lambda *a,**kw:'ok'))
            self.assertIn('active nahi',msg.reply.call_args.args[0]);self.assertEqual(self.store.load(),[])
        self.assertTrue(commands.followup(msg,'haan isi channel mein dena'))
        self.assertTrue(await commands.handle(msg,'haan isi channel mein dena',self.client,lambda *a,**kw:'ok'))
        self.assertEqual(self.store.load(),[])
    async def test_followup_scoping_ttl_no_arm(self):
        commands=sc.Commands(self.store,lambda *a:(self.target,False))
        msg=S(guild=self.guild,author=self.author,channel=self.dest,reply=AsyncMock(return_value=S(id=700)),reference=None)
        await commands.clarify(msg)
        self.assertTrue(commands.followup(msg,'okay'))
        for obj in (msg.author,msg.channel,msg.guild):
            old=obj.id;obj.id=777;self.assertFalse(commands.followup(msg,'okay'));obj.id=old
        msg.reference=S(message_id=999);self.assertFalse(commands.followup(msg,'okay'))
        msg.reference=S(message_id=700);self.assertTrue(commands.followup(msg,'okay'))
        commands.pending[commands.key(msg)]=(0,700);self.assertFalse(commands.followup(msg,'okay'))
    async def test_bounded_natural_self_reminder_creates_preview_only(self):
        commands=sc.Commands(self.store,lambda *a:(self.target,False))
        msg=S(guild=self.guild,author=self.author,channel=self.dest,created_at=dt.datetime.now(dt.timezone.utc),reply=AsyncMock(),reference=None)
        await commands.handle(msg,'reminder 10 min baad: Paani pi lena',self.client,lambda *a,**kw:'ok')
        self.assertEqual(self.store.load()[0]['status'],'preview');self.assertEqual(self.store.load()[0]['targets'],[1])
        self.assertEqual(self.store.load()[0]['body'],'<@1>\nPaani pi lena')
    async def test_own_failed_status_without_body_destination_or_targets(self):
        e=self.arm();data=self.store.load();data[0].update(status='failed',reason='access_or_member_check_failed');self.store.save(data)
        del self.members[2]
        commands=sc.Commands(self.store,lambda *a:(self.target,False));commands.health=lambda:'running'
        msg=S(guild=self.guild,author=self.author,channel=self.dest,reply=AsyncMock())
        await commands.handle(msg,'schedule status '+e['id'],self.client,lambda *a,**kw:'ok')
        result=msg.reply.call_args.args[0]
        self.assertIn('failed',result);self.assertIn('running',result);self.assertNotIn('hello',result);self.assertNotIn('<@2>',result)
        msg.author=S(id=3);await commands.handle(msg,'schedule list',self.client,lambda *a,**kw:'ok')
        self.assertNotIn(e['id'],msg.reply.call_args.args[0])
    async def test_ten_due_jobs_drained_same_tick_no_15s_each(self):
        for i in range(10):self.arm()
        await self.worker.tick(100)
        self.assertEqual(self.dest.send.await_count,10)
        self.assertTrue(all(e['status']=='sent' for e in self.store.load()))

class NaturalRepairTests(Schedules):
    def commands(self):
        return sc.Commands(self.store,lambda *a:(self.target,False))
    def msg(self):
        return S(guild=self.guild,author=self.author,channel=self.dest,
                 created_at=dt.datetime.now(dt.timezone.utc),reply=AsyncMock(return_value=S(id=700)),reference=None)
    def add_channel(self,cid=21,name='gaming-chat'):
        ch=S(id=cid,guild=self.guild,type=discord.ChannelType.text,name=name,send=AsyncMock(return_value=S(id=78)))
        ch.permissions_for=lambda m:S(view_channel=True,send_messages=True)
        channels={20:self.dest,cid:ch}
        self.guild.get_channel=lambda c:channels.get(c)
        return ch
    async def test_remind_me_here_after_minutes(self):
        message=self.msg()
        await self.commands().handle(message,'remind me here after 3 minute to play genshin',self.client,lambda *a,**kw:'ok')
        e=self.store.load()[0]
        self.assertEqual(e['status'],'preview');self.assertEqual(e['targets'],[1])
        self.assertEqual(e['dest'],20);self.assertEqual(e['source'],20)
        self.assertEqual(e['body'],'<@1>\nplay genshin')
        self.assertAlmostEqual(e['due'],message.created_at.timestamp()+180,delta=2)
        self.assertIn('abhi armed nahi',message.reply.call_args.args[0])
    async def test_remind_me_explicit_channel_and_username_note(self):
        self.add_channel();message=self.msg()
        await self.commands().handle(message,'remind me after 5 minutes by tagging me (my username: fazal_techiudex07) <#21> pe ki mujhe Khaana Khane Jaana hoga',self.client,lambda *a,**kw:'ok')
        e=self.store.load()[0]
        self.assertEqual(e['dest'],21);self.assertEqual(e['targets'],[1])
        self.assertEqual(e['body'],'<@1>\nmujhe Khaana Khane Jaana hoga')
    async def test_absent_channel_defaults_request_channel(self):
        message=self.msg()
        await self.commands().handle(message,'remind me after 10 minutes paani pi lena',self.client,lambda *a,**kw:'ok')
        self.assertEqual(self.store.load()[0]['dest'],20)
    async def test_leading_filler_before_keyword(self):
        message=self.msg()
        await self.commands().handle(message,'OK Kristy remind me after 5 minutes to drink water',self.client,lambda *a,**kw:'ok')
        e=self.store.load()[0]
        self.assertEqual(e['targets'],[1]);self.assertEqual(e['body'],'<@1>\ndrink water')
    async def test_clock_times_convert_and_past_rejected(self):
        commands=self.commands();message=self.msg()
        self.assertEqual(commands.natural_parts('remind','me at 9:30 PM to sleep',message),['aaj 9:30 PM','here','sleep'])
        self.assertEqual(commands.natural_parts('remind','me kal 8 PM call karna',message),['kal 8 PM','here','call karna'])
        anchor=dt.datetime(2026,11,2,19,0,tzinfo=tm.IST)
        self.assertEqual(dt.datetime.fromtimestamp(tm.parse_time('aaj 9:30 PM',anchor),tm.IST).isoformat(),'2026-11-02T21:30:00+05:30')
        with self.assertRaises(ValueError):tm.parse_time('aaj 6 PM',anchor)
    async def test_missing_time_still_clarifies_no_job(self):
        message=self.msg()
        self.assertTrue(await self.commands().handle(message,'remind me to play genshin',self.client,lambda *a,**kw:'ok'))
        self.assertIn('active nahi',message.reply.call_args.args[0]);self.assertEqual(self.store.load(),[])
    async def test_natural_preview_confirm_fires_once(self):
        commands=self.commands();message=self.msg()
        await commands.handle(message,'remind me after 5 minutes to stretch',self.client,lambda *a,**kw:'ok')
        e=self.store.load()[0]
        await commands.handle(message,'schedule confirm '+e['id'],self.client,lambda *a,**kw:'ok')
        await self.worker.tick(e['due']);await self.worker.tick(e['due']+30)
        self.dest.send.assert_awaited_once()
        self.assertEqual(self.dest.send.call_args.args[0],'<@1>\nstretch')
    async def test_reversed_order_intent_clarifies_not_ai(self):
        message=self.msg()
        self.assertTrue(await self.commands().handle(message,'| here | fazallettee | unhe tag kardena 10 min baad ke khana khane Jana hai | <@2> |',self.client,lambda *a,**kw:'ok'))
        self.assertIn('active nahi',message.reply.call_args.args[0]);self.assertEqual(self.store.load(),[])
    async def test_two_channel_mentions_clarify(self):
        self.add_channel();message=self.msg()
        await self.commands().handle(message,'remind me after 5 minutes <#20> <#21> kuch bhi',self.client,lambda *a,**kw:'ok')
        self.assertIn('active nahi',message.reply.call_args.args[0]);self.assertEqual(self.store.load(),[])
    async def test_natural_body_markers_rejected(self):
        message=self.msg()
        await self.commands().handle(message,'remind me after 5 minutes @everyone uth ja',self.client,lambda *a,**kw:'ok')
        self.assertEqual(self.store.load(),[])
    async def test_natural_summon_with_mentions(self):
        self.add_channel();message=self.msg()
        await self.commands().handle(message,'summon kal 8 PM <@2> <#21>',self.client,lambda *a,**kw:'ok')
        e=self.store.load()[0]
        self.assertEqual(e['targets'],[2]);self.assertEqual(e['dest'],21)
        self.assertEqual(e['body'],'<@2>\nVC join kar lo, creator ne is time par bulaya hai.')
