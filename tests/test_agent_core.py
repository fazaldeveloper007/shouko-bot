"""Persistent action core: ledger CRUD, pending restore across restarts,
and execution-truth recording for reminders, announcements and server plans."""
import datetime as dt
import os
import pathlib
import tempfile
import time
import types
import unittest
from unittest.mock import AsyncMock

import agent_core
import actions
import schedule_commands as sc
import timed_messages as tm


def make_ledger():
    tmp = tempfile.TemporaryDirectory()
    path = pathlib.Path(tmp.name) / 'ledger.sqlite3'
    return tmp, agent_core.TaskLedger(path), path


class LedgerTests(unittest.TestCase):
    def test_create_get_update(self):
        tmp, led, _ = make_ledger()
        self.addCleanup(tmp.cleanup)
        ident = led.create('reminder', agent_core.PENDING, guild=1, channel=2, author=3,
                           payload={'task': 'chai'}, expires=time.time() + 60)
        row = led.get(ident)
        self.assertEqual(row['kind'], 'reminder')
        self.assertEqual(row['status'], agent_core.PENDING)
        self.assertEqual(row['payload'], {'task': 'chai'})
        self.assertTrue(led.update(ident, status=agent_core.DONE, result='saved'))
        row = led.get(ident)
        self.assertEqual(row['status'], agent_core.DONE)
        self.assertEqual(row['result'], 'saved')
        self.assertFalse(led.update('missing', status=agent_core.DONE))

    def test_pending_skips_expired_and_settled(self):
        tmp, led, _ = make_ledger()
        self.addCleanup(tmp.cleanup)
        led.create('reminder_clarify', agent_core.PENDING, guild=1, channel=2, author=3,
                   payload={}, expires=time.time() + 60)
        led.create('reminder_clarify', agent_core.PENDING, guild=1, channel=2, author=4,
                   payload={}, expires=time.time() - 1)
        led.create('reminder_clarify', agent_core.CANCELLED, guild=1, channel=2, author=5,
                   payload={}, expires=time.time() + 60)
        rows = led.pending('reminder_clarify')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['author'], '3')
        self.assertGreaterEqual(led.purge_expired(), 1)
        self.assertEqual(led.pending('reminder_clarify'), [r for r in rows if r['author'] == '3'])

    def test_settle_key_settles_only_that_slot(self):
        tmp, led, _ = make_ledger()
        self.addCleanup(tmp.cleanup)
        led.create('reminder_clarify', agent_core.PENDING, guild=1, channel=2, author=3, payload={})
        led.create('reminder_confirm', agent_core.PENDING, guild=1, channel=2, author=3, payload={})
        led.create('reminder_clarify', agent_core.PENDING, guild=1, channel=2, author=9, payload={})
        n = led.settle_key(sc.Commands.PENDING_KINDS, 1, 2, 3, agent_core.DONE)
        self.assertEqual(n, 2)
        remaining = led.pending('reminder_clarify')
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]['author'], '9')

    def test_survives_reopen(self):
        tmp, led, path = make_ledger()
        self.addCleanup(tmp.cleanup)
        ident = led.create('announce_preview', agent_core.PENDING, guild=1, channel=2, author=3,
                           payload={'entry': {'text': 'hi'}}, expires=time.time() + 300)
        led.close()
        led2 = agent_core.TaskLedger(path)
        rows = led2.pending('announce_preview')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], ident)
        self.assertEqual(rows[0]['payload']['entry']['text'], 'hi')
        led2.close()


class PendingItemTests(unittest.TestCase):
    def test_pack_unpack_roundtrip(self):
        anchor = dt.datetime(2026, 11, 28, 21, tzinfo=dt.timezone.utc)
        item = (time.monotonic() + 600, 42, 'kal 9 baje', anchor, 'marker', {'x': 1})
        packed = agent_core.pack_pending_item(item)
        out = agent_core.unpack_pending_item(packed, agent_core.wall_from_monotonic(item[0]))
        self.assertEqual(out[1:], (42, 'kal 9 baje', anchor, 'marker', {'x': 1}))
        self.assertGreater(out[0], time.monotonic() + 590)

    def test_unpack_expired_clamps_to_now(self):
        packed = agent_core.pack_pending_item((time.monotonic() + 1, 42, 'x'))
        out = agent_core.unpack_pending_item(packed, time.time() - 5)
        self.assertAlmostEqual(out[0], time.monotonic(), delta=0.1)


class CommandsLedgerTests(unittest.TestCase):
    def make_commands(self, ledger):
        store = types.SimpleNamespace(load=lambda: [], save=lambda data: None)
        return sc.Commands(store, lambda *a: (None, False), ledger=ledger)

    def test_clarify_stash_and_restore_after_restart(self):
        tmp, led, path = make_ledger()
        self.addCleanup(tmp.cleanup)
        cmd = self.make_commands(led)
        key = (10, 20, 30)
        anchor = dt.datetime(2026, 11, 28, 21, tzinfo=dt.timezone.utc)
        item = (time.monotonic() + 600, 99, 'kal 9 baje study', anchor)
        cmd.pending[key] = item
        cmd._ledger_stash('reminder_clarify', key, item)
        led.close()
        # simulated restart: fresh Commands over the same ledger file
        cmd2 = self.make_commands(agent_core.TaskLedger(path))
        self.assertEqual(cmd2.pending, {})
        self.assertEqual(cmd2.restore_pending(), 1)
        restored = cmd2.pending[key]
        self.assertEqual(restored[1:], item[1:])
        self.assertGreater(restored[0], time.monotonic())

    def test_pending_pop_settles_ledger(self):
        tmp, led, _ = make_ledger()
        self.addCleanup(tmp.cleanup)
        cmd = self.make_commands(led)
        key = (10, 20, 30)
        item = (time.monotonic() + 600, 99, 'x', None)
        cmd.pending[key] = item
        cmd._ledger_stash('reminder_clarify', key, item)
        cmd._pending_pop(key, agent_core.DONE)
        self.assertEqual(cmd.pending, {})
        self.assertEqual(led.pending('reminder_clarify'), [])

    def test_restash_replaces_older_pending_for_slot(self):
        tmp, led, _ = make_ledger()
        self.addCleanup(tmp.cleanup)
        cmd = self.make_commands(led)
        key = (10, 20, 30)
        item = (time.monotonic() + 600, 99, 'x', None)
        cmd._ledger_stash('reminder_clarify', key, item)
        cmd._ledger_stash('reminder_confirm', key, item)
        rows = led.pending()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'reminder_confirm')

    def test_no_ledger_is_noop(self):
        cmd = self.make_commands(None)
        key = (10, 20, 30)
        cmd.pending[key] = (time.monotonic() + 600, 99, 'x', None)
        cmd._ledger_stash('reminder_clarify', key, cmd.pending[key])
        cmd._pending_pop(key)
        self.assertEqual(cmd.restore_pending(), 0)


class ActionsLedgerTests(unittest.TestCase):
    def make_actions(self, ledger):
        client = types.SimpleNamespace(music=None, get_channel=lambda cid: None)
        store = types.SimpleNamespace(load=lambda: [], save=lambda data: None)
        os.environ['KRISTY_SERVER_BUILDER'] = '0'
        self.addCleanup(lambda: os.environ.pop('KRISTY_SERVER_BUILDER', None))
        return actions.Actions(client, '111', store, ledger=ledger)

    def test_preview_stash_settle_restore(self):
        tmp, led, path = make_ledger()
        self.addCleanup(tmp.cleanup)
        act = self.make_actions(led)
        entry = {'author': 30, 'channel': 20, 'guild': 10, 'scope': 'everyone',
                 'text': 'hello', 'when': None, 'expires': time.monotonic() + 300}
        act.pending['abc123'] = entry
        act._ledger_stash_preview('abc123', entry)
        act._ledger_settle_preview('abc123', agent_core.CANCELLED)
        self.assertEqual(led.pending('announce_preview'), [])
        act._ledger_stash_preview('def456', entry)
        led.close()
        act2 = self.make_actions(agent_core.TaskLedger(path))
        self.assertEqual(act2.restore_pending(), 1)
        restored = act2.pending['def456']
        self.assertEqual(restored['text'], 'hello')
        self.assertGreater(restored['expires'], time.monotonic())

    def test_no_ledger_is_noop(self):
        act = self.make_actions(None)
        entry = {'author': 30, 'channel': 20, 'guild': 10, 'expires': time.monotonic() + 300}
        act.pending['abc'] = entry
        act._ledger_stash_preview('abc', entry)
        act._ledger_settle_preview('abc', agent_core.DONE)
        self.assertEqual(act.restore_pending(), 0)


class BuilderLedgerTests(unittest.TestCase):
    def test_plan_stash_settle_restore(self):
        import server_builder
        tmp, led, path = make_ledger()
        self.addCleanup(tmp.cleanup)
        builder = server_builder.Builder(types.SimpleNamespace(), ledger=led)
        entry = {'author': 30, 'channel': 20, 'guild': 10, 'steps': [{'name': 'x', 'destructive': False}],
                 'expires': time.monotonic() + 300}
        builder.pending['plan1'] = entry
        builder._ledger_stash_plan('plan1', entry)
        builder._ledger_settle_plan('plan1', agent_core.CANCELLED)
        self.assertEqual(led.pending('server_plan'), [])
        builder._ledger_stash_plan('plan2', entry)
        led.close()
        builder2 = server_builder.Builder(types.SimpleNamespace(), ledger=agent_core.TaskLedger(path))
        self.assertEqual(builder2.restore_pending(), 1)
        self.assertEqual(builder2.pending['plan2']['steps'][0]['name'], 'x')
        self.assertGreater(builder2.pending['plan2']['expires'], time.monotonic())


class RestartContinuationTests(unittest.TestCase):
    """Conversational continuation reads the restored shared state: a
    clarification staged before a restart resolves from the same pending
    item after restore, and settle writes the outcome to the ledger."""
    def fake_message(self, guild=10, channel=20, author=30, ref_id=None):
        return types.SimpleNamespace(
            guild=types.SimpleNamespace(id=guild),
            channel=types.SimpleNamespace(id=channel),
            author=types.SimpleNamespace(id=author),
            reference=None if ref_id is None else types.SimpleNamespace(message_id=ref_id),
            reply=AsyncMock())

    def test_clarify_reply_resolves_after_restart(self):
        tmp, led, path = make_ledger()
        self.addCleanup(tmp.cleanup)
        store = types.SimpleNamespace(load=lambda: [], save=lambda data: None)
        cmd = sc.Commands(store, lambda *a: (None, False), ledger=led)
        anchor = dt.datetime(2026, 11, 28, 21, tzinfo=dt.timezone.utc)
        msg = self.fake_message(ref_id=99)
        key = cmd.key(msg)
        item = (time.monotonic() + 600, 99, 'kal 9 baje study', anchor)
        cmd.pending[key] = item
        cmd._ledger_stash('reminder_clarify', key, item)
        led.close()
        # restart: fresh Commands over the same ledger, restore, then the
        # user's reply must resolve against the restored pending item
        cmd2 = sc.Commands(store, lambda *a: (None, False), ledger=agent_core.TaskLedger(path))
        self.assertEqual(cmd2.pending, {})
        self.assertEqual(cmd2.restore_pending(), 1)
        self.assertEqual(cmd2.pending_item(msg), cmd2.pending[key])
        cmd2._pending_pop(key, agent_core.DONE)
        self.assertEqual(cmd2.pending, {})
        self.assertEqual(cmd2.ledger.pending(), [])


class ActionsRestartTests(unittest.IsolatedAsyncioTestCase):
    async def test_restored_preview_settles_after_restart(self):
        tmp, led, path = make_ledger()
        self.addCleanup(tmp.cleanup)
        client = types.SimpleNamespace(music=None, get_channel=lambda cid: None)
        store = types.SimpleNamespace(load=lambda: [], save=lambda data: None)
        os.environ['KRISTY_SERVER_BUILDER'] = '0'
        self.addCleanup(lambda: os.environ.pop('KRISTY_SERVER_BUILDER', None))
        act = actions.Actions(client, '111', store, ledger=led)
        entry = {'author': 30, 'channel': 20, 'guild': 10, 'scope': 'here',
                 'text': 'hello', 'when': None, 'expires': time.monotonic() + 300}
        act.pending['abc123'] = entry
        act._ledger_stash_preview('abc123', entry)
        led.close()
        # restart: restore, then the owner's cancel must settle the ledger row
        act2 = actions.Actions(client, '111', store, ledger=agent_core.TaskLedger(path))
        self.assertEqual(act2.restore_pending(), 1)
        msg = types.SimpleNamespace(
            author=types.SimpleNamespace(id=30),
            channel=types.SimpleNamespace(id=20),
            guild=types.SimpleNamespace(id=10),
            reply=AsyncMock())
        handled = await act2._finish(msg, 'cancel', 'abc123', act2.pending['abc123'])
        self.assertTrue(handled)
        self.assertEqual(act2.pending, {})
        self.assertEqual(act2.ledger.get('abc123')['status'], agent_core.CANCELLED)


class WorkerRecorderTests(unittest.TestCase):
    def test_recorder_receives_delivery_entry(self):
        got = []
        store = types.SimpleNamespace(load=lambda: [], save=lambda data: None)
        worker = tm.Worker(types.SimpleNamespace(), store, recorder=got.append)
        entry = {'id': 'x', 'due': 123, 'status': 'sent', 'reason': None}
        worker._record(entry)
        self.assertEqual(got, [entry])
        worker2 = tm.Worker(types.SimpleNamespace(), store)
        worker2._record(entry)  # no recorder: no crash

    def test_recorder_failure_does_not_break_tick(self):
        def boom(e):
            raise RuntimeError('db down')
        store = types.SimpleNamespace(load=lambda: [], save=lambda data: None)
        worker = tm.Worker(types.SimpleNamespace(), store, recorder=boom)
        worker._record({'id': 'x'})  # logs, never raises


class VerifySavedTests(unittest.TestCase):
    def test_verify_saved(self):
        entry = {'id': 'abc', 'status': 'pending'}
        store = types.SimpleNamespace(load=lambda: [entry])
        self.assertEqual(agent_core.verify_saved(store, 'abc'), entry)
        self.assertIsNone(agent_core.verify_saved(store, 'missing'))
        bad = types.SimpleNamespace(load=lambda: (_ for _ in ()).throw(OSError('disk')))
        self.assertIsNone(agent_core.verify_saved(bad, 'abc'))


if __name__ == '__main__':
    unittest.main()
