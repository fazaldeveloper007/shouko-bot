"""Calendar recurrence without external calls or real reminders."""
import datetime as dt
import unittest
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace as S
import timed_messages as tm
import schedule_commands as sc
import test_weekly_reminders as wr

A=dt.datetime(2026,11,23,13,tzinfo=tm.IST)
class CalendarTimes(unittest.TestCase):
    def test_daily_calendar_rollover(self):
        for anchor,expected in ((A,'2026-11-23T21:00:00+05:30'),(A.replace(hour=22),'2026-11-24T21:00:00+05:30')):
            due,r=tm.recurrence_time('every day at 9 PM',anchor)
            self.assertEqual(dt.datetime.fromtimestamp(due,tm.IST).isoformat(),expected)
            self.assertEqual(r['kind'],'daily')
    def test_monthly_is_not_thirty_days(self):
        due,r=tm.recurrence_time('every month on 15 at 9 PM',A)
        self.assertEqual(dt.datetime.fromtimestamp(due,tm.IST).date(),dt.date(2026,12,15))
        after=tm.next_occurrence(r,due)
        self.assertEqual(dt.datetime.fromtimestamp(after,tm.IST).date(),dt.date(2027,1,15))
    def test_monthly_same_day_or_following_month(self):
        for hour,expected in ((13,dt.date(2026,11,23)),(22,dt.date(2026,12,23))):
            due,r=tm.recurrence_time('monthly on 23rd 9 PM',A.replace(hour=hour))
            self.assertEqual(dt.datetime.fromtimestamp(due,tm.IST).date(),expected)
    def test_29_to_31_require_policy_not_silent_skip_or_clamp(self):
        for day in (29,30,31):
            with self.assertRaisesRegex(ValueError,'short-month policy'):
                tm.recurrence_time(f'every month on {day} 9 PM',A)
    def test_monthly_1_and_15_always_exist_including_leap_year(self):
        for year in (2027,2028):
            for day in (1,15):
                due,r=tm.recurrence_time(f'every month on {day} 9 PM',dt.datetime(year,1,28,tzinfo=tm.IST))
                self.assertEqual(dt.datetime.fromtimestamp(due,tm.IST).date(),dt.date(year,2,day))
    def test_invalid_time_and_date(self):
        for when in ('daily 25 PM','daily 9:90 PM','every month on 0 9 PM','every month on 32 9 PM','every month 9 PM','monthly on monday 9 PM'):
            with self.assertRaises(ValueError):tm.recurrence_time(when,A)
    def test_daily_natural_grounding_and_wrong_model(self):
        for text in ('remind me every day at 9 PM to study','remind me daily 9 PM to study','remind me har din 9 PM study'):
            self.assertIsNotNone(sc.pick_recurrence('every day 9 PM',[(text,A)])[0])
            self.assertIsNone(sc.pick_recurrence('aaj 9 PM',[(text,A)])[0])
            self.assertIsNone(sc.pick_recurrence('every monday 9 PM',[(text,A)])[0])
    def test_monthly_human_date_agrees_with_model(self):
        for text in ('every month on 15th at 9 PM study','monthly on the 15th at 9 PM study','har mahine 15 ko 9 PM study'):
            self.assertIsNotNone(sc.pick_recurrence('every month on 15 9 PM',[(text,A)])[0],text)
            self.assertIsNone(sc.pick_recurrence('every month on 16 9 PM',[(text,A)])[0])
    def test_incomplete_or_unsupported_does_not_save(self):
        for text in ('monthly 9 PM','every month on 15 and 16 at 9 PM','daily and every Monday 9 PM','every day at 9 PM PST','every 2 days at 9 PM','every month on 15 at 9 PM until December','every month on last day at 9 PM','monthly on first Monday 9 PM','daily 9 PM for 3 days'):
            self.assertIsNone(sc.pick_recurrence('every day 9 PM',[(text,A)])[0],text)
    def test_monthly_followup_date_and_time(self):
        self.assertIsNotNone(sc.pick_recurrence('every month on 15 9 PM',[('remind me monthly to study',A),('on 15 at 9 PM',A)])[0])
    def test_recurrence_storage_validation(self):
        for r in ({},dict(kind='daily',hour=9,minute=0,timezone='UTC'),dict(kind='monthly',hour=9,minute=0,timezone='Asia/Kolkata',day=32,missing_day='skip')):
            self.assertFalse(tm.valid_recurrence(r))

class DailyWorker(wr.WeeklyWorker):
    def setUp(self):
        super().setUp()
        data=self.store.load();self.due,self.r=tm.recurrence_time('daily 9 PM',A)
        data[0].update(due=self.due,recurrence=self.r);self.store.save(data)
    async def test_missed_backlog_skipped_in_one_tick(self):
        await self.worker.tick(self.due+30*86400+121)
        e=self.store.load()[0];self.assertEqual(e['last_status'],'missed');self.dest.send.assert_not_awaited()
    # Inherited tests with weekly-specific expected due/label are not applicable.
    async def test_send_next_week_stable_id_and_persist(self):
        await self.worker.tick(self.due);await self.worker.tick(self.due)
        e=tm.Store(self.store.path).load()[0];self.assertEqual(e['due'],self.due+86400)
        self.dest.send.assert_awaited_once()
    async def test_restart_dispatching_unknown_not_replayed(self):
        data=self.store.load();data[0]['status']='dispatching';self.store.save(data)
        import asyncio
        with patch('timed_messages.time.time',return_value=self.due+20),patch.object(self.worker,'tick',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await self.worker.run()
        e=self.store.load()[0];self.assertEqual(e['last_status'],'uncertain');self.assertEqual(e['due'],self.due+86400)
    async def test_restart_after_weeks_within_current_grace_sends_current_only(self):
        await self.worker.tick(self.due+14*86400+20)
        self.dest.send.assert_awaited_once();self.assertEqual(self.store.load()[0]['due'],self.due+15*86400)
    async def test_command_weekly_and_list_cancel(self):
        self.store.path.unlink();c=sc.Commands(self.store,lambda *a:(None,False))
        c.intent=AsyncMock(return_value=dict(action='remind',when='daily 9 PM',task='study',tag_names=[]))
        m=S(guild=self.guild,channel=self.dest,author=self.author,created_at=A,reference=None,reply=AsyncMock(return_value=S(id=700)))
        with patch('schedule_commands.time.time',return_value=A.timestamp()):await c.handle(m,'remind me every day at 9 PM to study',self.client,lambda *a,**k:'ok')
        e=self.store.load()[0];self.assertEqual(e['recurrence'],self.r);self.assertIn(e['id'],m.reply.call_args.args[0])
        await c.handle(m,'schedule cancel '+e['id'],self.client,lambda *a,**k:'ok');self.assertEqual(self.store.load()[0]['status'],'cancelled')
    def test_invalid_recurrence_stops_read(self):
        data=self.store.load();data[0]['recurrence']['hour']=25;self.store.save(data)
        with self.assertRaises(ValueError):self.store.load()
    async def test_conversational_list_keeps_series_after_first_occurrence(self):
        import actions
        await self.worker.tick(self.due)
        text=actions.Actions(self.client,'',self.store)._reminder_list(S(guild=self.guild,author=self.author))
        self.assertIn(self.e['id'],text);self.assertIn('Every day',text)

class MonthlyWorker(DailyWorker):
    def setUp(self):
        super().setUp();self.due,self.r=tm.recurrence_time('every month on 23 9 PM',A)
        data=self.store.load();data[0].update(due=self.due,recurrence=self.r);self.store.save(data)
    async def test_send_next_week_stable_id_and_persist(self):
        await self.worker.tick(self.due);await self.worker.tick(self.due)
        e=tm.Store(self.store.path).load()[0]
        self.assertEqual(dt.datetime.fromtimestamp(e['due'],tm.IST).date(),dt.date(2026,12,23));self.dest.send.assert_awaited_once()
    async def test_restart_dispatching_unknown_not_replayed(self):
        data=self.store.load();data[0]['status']='dispatching';self.store.save(data)
        import asyncio
        with patch('timed_messages.time.time',return_value=self.due+20),patch.object(self.worker,'tick',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await self.worker.run()
        self.assertEqual(self.store.load()[0]['due'],tm.next_occurrence(self.r,self.due));self.dest.send.assert_not_awaited()
    async def test_restart_after_weeks_within_current_grace_sends_current_only(self):
        clock=dt.datetime(2027,2,23,21,0,20,tzinfo=tm.IST).timestamp()
        await self.worker.tick(clock);self.dest.send.assert_awaited_once()
        self.assertEqual(dt.datetime.fromtimestamp(self.store.load()[0]['due'],tm.IST).date(),dt.date(2027,3,23))
    async def test_command_weekly_and_list_cancel(self):
        self.store.path.unlink();c=sc.Commands(self.store,lambda *a:(None,False))
        c.intent=AsyncMock(return_value=dict(action='remind',when='every month on 23 9 PM',task='study',tag_names=[]))
        m=S(guild=self.guild,channel=self.dest,author=self.author,created_at=A,reference=None,reply=AsyncMock(return_value=S(id=700)))
        with patch('schedule_commands.time.time',return_value=A.timestamp()):await c.handle(m,'remind me every month on 23 at 9 PM to study',self.client,lambda *a,**k:'ok')
        e=self.store.load()[0];self.assertEqual(e['recurrence'],self.r);self.assertIn(e['id'],m.reply.call_args.args[0])
        await c.handle(m,'schedule cancel '+e['id'],self.client,lambda *a,**k:'ok');self.assertEqual(self.store.load()[0]['status'],'cancelled')
    async def test_conversational_list_keeps_series_after_first_occurrence(self):
        import actions
        await self.worker.tick(self.due)
        text=actions.Actions(self.client,'',self.store)._reminder_list(S(guild=self.guild,author=self.author))
        self.assertIn(self.e['id'],text);self.assertIn('Every month',text)
