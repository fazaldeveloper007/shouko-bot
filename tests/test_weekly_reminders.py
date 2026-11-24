"""Weekly series tests use temp storage and fake Discord only."""
import asyncio
import datetime as dt
import pathlib
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace as S
import timed_messages as tm
import schedule_commands as sc

A=dt.datetime(2026,11,23,13,0,tzinfo=tm.IST)  # calculated Monday
class WeeklyTime(unittest.TestCase):
    def test_next_monday_and_week_rollover(self):
        self.assertEqual(A.weekday(),0)
        due,r=tm.weekly_time('every Monday at 9 PM',A)
        self.assertEqual(dt.datetime.fromtimestamp(due,tm.IST).isoformat(),'2026-11-23T21:00:00+05:30')
        due,r=tm.weekly_time('har monday 9 PM',A.replace(hour=22))
        self.assertEqual(dt.datetime.fromtimestamp(due,tm.IST).isoformat(),'2026-11-30T21:00:00+05:30')
    def test_exact_due_means_next_week(self):
        due,_=tm.weekly_time('every monday 9 PM',A.replace(hour=21))
        self.assertEqual(due,A.replace(hour=21).timestamp()+7*86400)
    def test_bad_times(self):
        for s in ('every monday 9','every monday 25 PM','every monday 9:90 PM','every weekday 9 PM'):
            with self.assertRaises(ValueError):tm.weekly_time(s,A)
    def test_human_grounded_and_no_downgrade(self):
        for ai in ('aaj 9 PM','every tuesday 9 PM','every monday 8 PM'):
            self.assertIsNone(sc.pick_weekly(ai,[('remind me every monday at 9 PM to study',A)])[0])
        self.assertIsNotNone(sc.pick_weekly('every monday 9 PM',[('remind me har monday at 9 PM to study',A)])[0])
    def test_unsupported_does_not_silently_change_request(self):
        for text in ('every monday and tuesday 9 PM','every other monday 9 PM','every monday 9 PM until December','every monday 9 PM PST','every monday starting 2026-12-01 at 9 PM','every monday 9 PM for 3 weeks','every monday 9 PM and 10 PM'):
            self.assertIsNone(sc.pick_weekly('every monday 9 PM',[(text,A)])[0],text)
    def test_followup_keeps_weekday(self):
        self.assertIsNotNone(sc.pick_weekly('every monday 9 PM',[('remind me every monday to study',A),('at 9 PM',A)])[0])

class WeeklyWorker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=tm.Store(pathlib.Path(self.tmp.name)/'s.json')
        self.author=S(id=1,bot=False);self.guild=S(id=10,unavailable=False,me=S(id=99))
        self.dest=S(id=20,guild=self.guild,type=__import__('discord').ChannelType.text,send=AsyncMock(return_value=S(id=7)))
        self.dest.permissions_for=lambda m:S(view_channel=True,send_messages=True)
        self.guild.get_member=lambda i:self.author if i==1 else None
        self.guild.get_channel=lambda i:self.dest if i==20 else None
        self.client=S(is_ready=lambda:True,get_guild=lambda i:self.guild)
        self.worker=tm.Worker(self.client,self.store)
        self.due,self.r=tm.weekly_time('every monday 9 PM',A)
        self.e=self.store.create(guild=10,author=1,source=20,dest=20,targets=[1],body='study',due=self.due,now=A.timestamp(),armed=True,recurrence=self.r)
    async def test_send_next_week_stable_id_and_persist(self):
        await self.worker.tick(self.due);await self.worker.tick(self.due)
        self.dest.send.assert_awaited_once()
        e=tm.Store(self.store.path).load()[0]
        self.assertEqual((e['id'],e['status'],e['last_status'],e['due']),(self.e['id'],'scheduled','sent',self.due+7*86400))
        await tm.Worker(self.client,tm.Store(self.store.path)).tick(e['due'])
        self.assertEqual(self.dest.send.await_count,2)
    async def test_missed_backlog_skipped_in_one_tick(self):
        await self.worker.tick(self.due+30*86400)
        e=self.store.load()[0];self.assertGreater(e['due'],self.due+30*86400)
        self.assertEqual(e['last_status'],'missed');self.dest.send.assert_not_awaited()
    async def test_daily_api_quota_uses_saved_fallback_and_keeps_weekly_series(self):
        import retrying
        data=self.store.load();data[0]['core']='save resin for weekly bosses'
        data[0]['body']='<@1>\nReminder: save resin for weekly bosses'
        self.store.save(data)
        async def quota(core):
            raise retrying.RequestError('Gemini',429,reason='quota')
        self.worker.rewrite=quota
        await self.worker.tick(self.due)
        await self.worker.tick(self.due+15)
        self.dest.send.assert_awaited_once()
        self.assertEqual(self.dest.send.call_args.args[0],data[0]['body'])
        saved=self.store.load()[0]
        self.assertEqual(saved['last_status'],'sent')
        self.assertEqual(saved['due'],tm.next_occurrence(self.r,self.due))
        self.assertFalse(self.worker.halted)

    async def test_timeout_advances_not_replayed(self):
        self.dest.send.side_effect=asyncio.TimeoutError
        await self.worker.tick(self.due);await self.worker.tick(self.due+20)
        self.dest.send.assert_awaited_once();self.assertEqual(self.store.load()[0]['last_status'],'uncertain')
    async def test_restart_dispatching_unknown_not_replayed(self):
        data=self.store.load();data[0]['status']='dispatching';self.store.save(data)
        with patch('timed_messages.time.time',return_value=self.due+20),patch.object(self.worker,'tick',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await self.worker.run()
        e=self.store.load()[0];self.assertEqual(e['last_status'],'uncertain');self.assertEqual(e['due'],self.due+7*86400)
    async def test_cancel_series_no_future_send(self):
        self.store.control(self.e['id'],10,1,'cancel',self.due-1)
        await self.worker.tick(self.due);await self.worker.tick(self.due+7*86400)
        self.dest.send.assert_not_awaited()
    async def test_cancel_while_sending_stops_future_without_false_claim(self):
        async def send(*a,**k):
            e=self.store.control(self.e['id'],10,1,'cancel',self.due)
            self.assertTrue(e['cancel_after_send']);return S(id=7)
        self.dest.send.side_effect=send
        await self.worker.tick(self.due);await self.worker.tick(self.due+7*86400)
        self.dest.send.assert_awaited_once();self.assertEqual(self.store.load()[0]['status'],'cancelled')
    async def test_access_failure_stops_series(self):
        self.dest.permissions_for=lambda m:S(view_channel=False,send_messages=False)
        await self.worker.tick(self.due);self.assertEqual(self.store.load()[0]['status'],'failed')
        await self.worker.tick(self.due+7*86400);self.dest.send.assert_not_awaited()
    async def test_cancel_during_rewrite(self):
        data=self.store.load();data[0]['core']='study';self.store.save(data)
        async def rewrite(core):self.store.control(self.e['id'],10,1,'cancel',self.due);return 'study now'
        self.worker.rewrite=rewrite
        await self.worker.tick(self.due);self.dest.send.assert_not_awaited()
    def test_invalid_recurrence_stops_read(self):
        data=self.store.load();data[0]['recurrence']['weekday']=1;self.store.save(data)
        with self.assertRaises(ValueError):self.store.load()
    async def test_wrong_person_or_guild_cannot_cancel(self):
        for g,u in ((11,1),(10,2)):
            with self.assertRaises(ValueError):self.store.control(self.e['id'],g,u,'cancel',self.due)
    async def test_command_weekly_and_list_cancel(self):
        self.store.path.unlink()
        c=sc.Commands(self.store,lambda *a:(None,False));c.intent=AsyncMock(return_value=dict(action='remind',when='every monday 9 PM',task='study',tag_names=[]))
        m=S(guild=self.guild,channel=self.dest,author=self.author,created_at=A,reference=None,reply=AsyncMock(return_value=S(id=700)))
        with patch('schedule_commands.time.time',return_value=A.timestamp()):
            await c.handle(m,'remind me every Monday at 9 PM to study',self.client,lambda *a,**k:'ok')
        e=self.store.load()[0];self.assertEqual(e['recurrence'],self.r);self.assertIn(e['id'],m.reply.call_args.args[0]);self.assertIn('IST',m.reply.call_args.args[0])
        await c.handle(m,'schedule list',self.client,lambda *a,**k:'ok');self.assertIn('Every Monday',m.reply.call_args.args[0])
        await c.handle(m,'schedule cancel '+e['id'],self.client,lambda *a,**k:'ok');self.assertEqual(self.store.load()[0]['status'],'cancelled')
    async def test_ai_outage_cannot_turn_weekly_into_one_shot(self):
        self.store.path.unlink();c=sc.Commands(self.store,lambda *a:(None,False));c.intent=AsyncMock(side_effect=RuntimeError)
        m=S(guild=self.guild,channel=self.dest,author=self.author,created_at=A,reference=None,reply=AsyncMock(return_value=S(id=700)))
        with patch('schedule_commands.time.time',return_value=A.timestamp()):await c.handle(m,'remind me every Monday at 9 PM to study',self.client,lambda *a,**k:'ok')
        self.assertEqual(self.store.load(),[])

    async def test_restart_after_weeks_within_current_grace_sends_current_only(self):
        await self.worker.tick(self.due+14*86400+20)
        self.dest.send.assert_awaited_once()
        e=self.store.load()[0];self.assertEqual(e['last_due'],self.due+14*86400)
        self.assertEqual(e['due'],self.due+21*86400)
    async def test_checkpoint_failure_sends_nothing(self):
        original=self.store.save
        def save(data):
            if any(e['status']=='dispatching' for e in data):raise OSError()
            original(data)
        with patch.object(self.store,'save',side_effect=save):
            with self.assertRaises(OSError):await self.worker.tick(self.due)
        self.dest.send.assert_not_awaited()
    async def test_final_write_failure_then_restart_never_replays(self):
        original=self.store.save
        def save(data):
            if any(e.get('last_status')=='sent' for e in data):raise OSError()
            original(data)
        with patch.object(self.store,'save',side_effect=save):
            with self.assertRaises(OSError):await self.worker.tick(self.due)
        self.assertEqual(self.store.load()[0]['status'],'dispatching')
        restarted=tm.Worker(self.client,self.store)
        with patch('timed_messages.time.time',return_value=self.due+20),patch.object(restarted,'tick',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await restarted.run()
        self.assertEqual(self.store.load()[0]['last_status'],'uncertain');self.dest.send.assert_awaited_once()

    async def test_conversational_list_keeps_series_after_first_occurrence(self):
        import actions
        await self.worker.tick(self.due)
        a=actions.Actions(self.client,'',self.store)
        text=a._reminder_list(S(guild=self.guild,author=self.author))
        self.assertIn(self.e['id'],text);self.assertIn('Every Monday',text)
    async def test_ambiguous_model_one_shot_is_not_saved(self):
        self.store.path.unlink();c=sc.Commands(self.store,lambda *a:(None,False))
        c.intent=AsyncMock(return_value=dict(action='remind',when='aaj 9 PM',task='study',tag_names=[]))
        m=S(guild=self.guild,channel=self.dest,author=self.author,created_at=A,reference=None,reply=AsyncMock(return_value=S(id=700)))
        with patch('schedule_commands.time.time',return_value=A.timestamp()):await c.handle(m,'remind me every Monday at 9 PM to study',self.client,lambda *a,**k:'ok')
        self.assertEqual(self.store.load(),[])
