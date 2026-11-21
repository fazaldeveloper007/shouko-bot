"""Zero-grace event kick: fresh help bids process immediately (offline)."""
import asyncio
import importlib.util
import os
import pathlib
import tempfile
import unittest
import warnings
from unittest.mock import patch

ROOT = pathlib.Path(__file__).parents[1]


def load_bot():
    env = {'DISCORD_TOKEN': 'OFFLINE_DUMMY', 'GEMINI_API_KEY': 'OFFLINE_DUMMY',
           'MUSIC_ENABLED': '0', 'MUSIC_SYNC_COMMANDS': '0'}
    with tempfile.TemporaryDirectory() as tmp:
        env['MEMORY_DIR'] = tmp
        with patch.dict(os.environ, env), warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=DeprecationWarning)
            spec = importlib.util.spec_from_file_location('offline_bot_kick', ROOT / 'bot.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module


class FakeChannel:
    def __init__(self, cid):
        self.id = cid
        self.sent = []

    async def send(self, text, allowed_mentions=None):
        self.sent.append(text)

    def history(self, limit=10):
        class _Empty:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration
        return _Empty()


class Kick(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {'MEMORY_DIR': self.tmp.name}
        with patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location('offline_pro_kick', ROOT / 'proactive.py')
            self.pro = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.pro)
        self.t = [1000000.0]
        self.p = self.pro.Proactive(state=self.pro.Proactive()._default(), now=lambda: self.t[0])
        self.bot._proactive = self.p
        self.ch = FakeChannel(10)
        self.bot._proactive_channels = {"gaming-chat": self.ch}
        self.bot._proactive_inflight.clear()
        self._enabled = patch.object(self.bot.proactive, "ENABLED", True)
        self._quiet = patch.object(self.p, "quiet_now", lambda *a, **k: False)
        self._enabled.start()
        self._quiet.start()
        self.addCleanup(self._enabled.stop)
        self.addCleanup(self._quiet.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, self.bot, "_proactive", None)
        self.addCleanup(setattr, self.bot, "_proactive_channels", {})

    def decide(self, reply="try this team", answered=False, can_help=True):
        async def fake(question, author, hist):
            return {"answered": answered, "can_help": can_help, "reply": reply}
        return patch.object(self.bot, "_proactive_decide", fake)

    async def test_fresh_bid_posts_immediately_at_zero_grace(self):
        self.assertEqual(self.bot.proactive.GRACE_MINUTES, 0)  # env unset in harness
        self.assertTrue(self.p.track(10, 100, 42, "best weapon for neuvillette?"))
        with self.decide():
            await self.bot._proactive_kick(self.ch, "gaming-chat")
        self.assertEqual(len(self.ch.sent), 1)
        self.assertIn("try this team", self.ch.sent[0])
        self.assertEqual(self.p.state["pending"], {})  # posted, no longer pending
        today_count = sum(self.p.state["daily"].values())
        self.assertEqual(today_count, 1)

    async def test_kick_skips_pending_already_inflight(self):
        self.p.track(10, 100, 42, "best weapon for neuvillette?")
        self.bot._proactive_inflight.add("10:42")  # loop or another kick is on it
        with self.decide():
            await self.bot._proactive_kick(self.ch, "gaming-chat")
        self.assertEqual(self.ch.sent, [])
        self.assertEqual(len(self.p.state["pending"]), 1)  # untouched for the owner

    async def test_kick_respects_quiet_hours(self):
        self.p.track(10, 100, 42, "best weapon for neuvillette?")
        with patch.object(self.p, "quiet_now", lambda *a, **k: True):
            with self.decide():
                await self.bot._proactive_kick(self.ch, "gaming-chat")
        self.assertEqual(self.ch.sent, [])
        self.assertEqual(len(self.p.state["pending"]), 1)  # still queued for later

    async def test_kick_respects_cooldown_after_a_post(self):
        self.p.track(10, 100, 42, "best weapon for neuvillette?")
        with self.decide():
            await self.bot._proactive_kick(self.ch, "gaming-chat")
        self.assertEqual(len(self.ch.sent), 1)
        # a second bid inside the cooldown window does not post
        self.p.track(10, 101, 77, "koi team review karega?")
        with self.decide(reply="second answer"):
            await self.bot._proactive_kick(self.ch, "gaming-chat")
        self.assertEqual(len(self.ch.sent), 1)  # unchanged: cooldown gate held
        self.assertEqual(len(self.p.state["pending"]), 1)  # second bid still queued

    async def test_kick_drops_when_verdict_cannot_help(self):
        self.p.track(10, 100, 42, "best weapon for neuvillette?")
        with self.decide(can_help=False, reply=""):
            await self.bot._proactive_kick(self.ch, "gaming-chat")
        self.assertEqual(self.ch.sent, [])
        self.assertEqual(self.p.state["pending"], {})  # dropped, not retried forever

    async def test_kick_drops_when_pending_cancelled_while_deciding(self):
        self.p.track(10, 100, 42, "best weapon for neuvillette?")

        async def cancelling_decide(question, author, hist):
            # her direct reply covered it while the AI was thinking
            self.p.state["pending"].pop("10:42", None)
            return {"answered": False, "can_help": True, "reply": "try this team"}

        with patch.object(self.bot, "_proactive_decide", cancelling_decide):
            await self.bot._proactive_kick(self.ch, "gaming-chat")
        self.assertEqual(self.ch.sent, [])  # no double-answer

    async def test_kick_drops_when_reply_landed_while_deciding(self):
        self.p.track(10, 100, 42, "best weapon for neuvillette?")

        class AnsweredChannel(FakeChannel):
            def history(self, limit=10):
                import types
                ref = types.SimpleNamespace(message_id=100)
                msg = types.SimpleNamespace(reference=ref)

                class _One:
                    def __init__(self):
                        self.done = False

                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        if self.done:
                            raise StopAsyncIteration
                        self.done = True
                        return msg
                return _One()

        ch = AnsweredChannel(10)
        self.bot._proactive_channels = {"gaming-chat": ch}
        with self.decide():
            await self.bot._proactive_kick(ch, "gaming-chat")
        self.assertEqual(ch.sent, [])
        self.assertEqual(self.p.state["pending"], {})  # dropped: already answered


if __name__ == '__main__':
    unittest.main()


class GreetingBids(unittest.TestCase):
    """Short greetings seeking acknowledgement count as response bids; the AI
    verdict at expiry remains the gatekeeper for whether she posts."""

    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def test_short_greeting_is_a_bid(self):
        pro = self.bot.proactive
        self.assertTrue(pro.looks_like_help_bid("hello"))
        self.assertTrue(pro.looks_like_help_bid("Hi koi hai"))
        self.assertTrue(pro.looks_like_help_bid("good morning guys"))
        self.assertTrue(pro.looks_like_help_bid("namaste"))

    def test_non_bids_stay_untracked(self):
        pro = self.bot.proactive
        self.assertFalse(pro.looks_like_help_bid(""))
        self.assertFalse(pro.looks_like_help_bid("lol"))
        self.assertFalse(pro.looks_like_help_bid("checkout my new clip"))
        self.assertFalse(pro.looks_like_help_bid(
            "hello everyone today we push rank and grind the new event together"))


class DelayedKick(unittest.IsolatedAsyncioTestCase):
    """Grace-delayed kick: fires after the wait, re-checks every gate then."""

    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {'MEMORY_DIR': self.tmp.name}
        with patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location('offline_pro_dkick', ROOT / 'proactive.py')
            self.pro = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.pro)
        self.t = [1000000.0]
        self.p = self.pro.Proactive(state=self.pro.Proactive()._default(), now=lambda: self.t[0])
        self.bot._proactive = self.p
        self.ch = FakeChannel(10)
        self.bot._proactive_channels = {"gaming-chat": self.ch}
        self.bot._proactive_inflight.clear()
        self._enabled = patch.object(self.bot.proactive, "ENABLED", True)
        self._grace = patch.object(self.pro, "GRACE_MINUTES", 1)
        self._quiet = patch.object(self.p, "quiet_now", lambda *a, **k: False)
        self._enabled.start()
        self._grace.start()
        self._quiet.start()
        self.addCleanup(self._enabled.stop)
        self.addCleanup(self._grace.stop)
        self.addCleanup(self._quiet.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, self.bot, "_proactive", None)
        self.addCleanup(setattr, self.bot, "_proactive_channels", {})

    def decide(self, reply="try this team", answered=False, can_help=True):
        async def fake(question, author, hist):
            return {"answered": answered, "can_help": can_help, "reply": reply}
        return patch.object(self.bot, "_proactive_decide", fake)

    async def test_scheduled_kick_posts_due_pending(self):
        self.assertTrue(self.p.track(10, 100, 42, "best weapon for neuvillette?"))
        self.p.state["pending"]["10:42"]["ts"] = self.t[0] - 61  # grace already over
        with self.decide():
            self.bot._schedule_proactive_kick(self.ch, "gaming-chat", 0.05)
            await asyncio.sleep(0.3)
        self.assertEqual(len(self.ch.sent), 1)
        self.assertEqual(self.p.state["pending"], {})

    async def test_scheduled_kick_leaves_early_pending(self):
        self.assertTrue(self.p.track(10, 100, 42, "best weapon for neuvillette?"))
        # ts is now: under a 1-minute grace the bid is NOT due when the kick fires
        with self.decide():
            self.bot._schedule_proactive_kick(self.ch, "gaming-chat", 0.05)
            await asyncio.sleep(0.3)
        self.assertEqual(self.ch.sent, [])
        self.assertEqual(len(self.p.state["pending"]), 1)

    async def test_scheduled_kick_skips_cancelled_pending(self):
        self.assertTrue(self.p.track(10, 100, 42, "hello"))
        self.p.state["pending"]["10:42"]["ts"] = self.t[0] - 61
        self.p.cancel_if_addressed(10, 99, 100)  # someone reply-referenced it meanwhile
        with self.decide():
            self.bot._schedule_proactive_kick(self.ch, "gaming-chat", 0.05)
            await asyncio.sleep(0.3)
        self.assertEqual(self.ch.sent, [])
        self.assertEqual(self.p.state["pending"], {})

    async def test_zero_delay_kick_behaves_like_before(self):
        self.assertTrue(self.p.track(10, 100, 42, "best weapon for neuvillette?"))
        self.p.state["pending"]["10:42"]["ts"] = self.t[0] - 61
        with self.decide():
            self.bot._schedule_proactive_kick(self.ch, "gaming-chat", 0)
            await asyncio.sleep(0.1)
        self.assertEqual(len(self.ch.sent), 1)
