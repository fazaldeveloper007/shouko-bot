"""Operational evidence only: no live Discord calls or historical cause guesses."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace as S
import test_weekly_reminders as weekly
import timed_messages as tm
import schedule_commands as sc

class Diagnostics(weekly.WeeklyWorker):
    async def test_skip_records_stage_clock_gap_and_connection(self):
        self.worker.started_at=self.due-600
        await self.worker.tick(self.due-20)
        self.worker.connected=lambda:False
        await self.worker.tick(self.due+121)
        e=self.store.load()[0];d=e['last_diagnostic']
        self.assertEqual(d,dict(checked_at=self.due+121,due=self.due,lateness_seconds=121,
            stage='before_send',worker_started_at=self.due-600,previous_tick_at=self.due-20,
            tick_gap_seconds=141,connected=False,ready=True))
        self.assertEqual(e['last_status'],'missed')
        self.dest.send.assert_not_awaited()
        self.assertEqual(tm.Store(self.store.path).load()[0]['last_diagnostic'],d)
    async def test_after_wording_skip_is_distinguishable(self):
        data=self.store.load();data[0]['core']='study';self.store.save(data)
        clock=[self.due]
        async def rewrite(core):
            clock[0]=self.due+121
            return 'study now'
        self.worker.rewrite=rewrite
        with patch('timed_messages.time.time',side_effect=lambda:clock[0]):
            await self.worker.tick()
        self.assertEqual(self.store.load()[0]['last_diagnostic']['stage'],'after_wording')
        self.dest.send.assert_not_awaited()
    async def test_status_has_evidence_without_task_or_audience(self):
        await self.worker.tick(self.due+130)
        c=sc.Commands(self.store,lambda *a:(None,False));c.health=self.worker.health
        m=S(guild=self.guild,author=self.author,channel=self.dest,reply=AsyncMock())
        await c.handle(m,'schedule status '+self.e['id'],self.client,lambda *a,**kw:'ok')
        text=m.reply.call_args.args[0]
        for expected in ('late 130.0s','stage=before_send','connected=True','not a root-cause'):
            self.assertIn(expected,text)
        for hidden in ('study','<@1>','targets=','dest='):
            self.assertNotIn(hidden,text)
    def test_legacy_and_malformed_diagnostics_are_not_invented(self):
        self.assertIn('not recorded',tm.diagnostic_summary(self.e))
        for d in (None,{'checked_at':float('nan')},{'checked_at':10**100,'due':0,'lateness_seconds':0}):
            self.assertIn('Timing detail:',tm.diagnostic_summary(dict(last_diagnostic=d)))
    async def test_exact_grace_boundary_is_not_skipped(self):
        await self.worker.tick(self.due+120)
        self.dest.send.assert_awaited_once()
        self.assertNotIn('last_diagnostic',self.store.load()[0])
