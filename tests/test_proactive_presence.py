"""Proactive unanswered-help rules + presence snapshot gating (offline)."""
import datetime
import importlib.util
import os
import pathlib
import tempfile
import types
import unittest
import warnings
from unittest.mock import patch

ROOT = pathlib.Path(__file__).parents[1]


def load_source(spec, module):
    # discord.py2.7.1 event decorator uses a Python3.14-deprecated alias.
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message=r"^'asyncio\.iscoroutinefunction' is deprecated and slated for removal in Python 3\.16; use inspect\.iscoroutinefunction\(\) instead$", category=DeprecationWarning, module=r'^discord\.client$')
        spec.loader.exec_module(module)


def member(mid, name, status="online", bot=False, activity=None):
    m = types.SimpleNamespace(id=mid, display_name=name, name=name.lower(),
                              bot=bot, status=status, activities=[activity] if activity else [])
    return m


def activity(kind="playing", name="Genshin Impact", start=None, details=""):
    return types.SimpleNamespace(type=types.SimpleNamespace(name=kind), name=name,
                                 start=start, details=details, state="")


class Presence(unittest.IsolatedAsyncioTestCase):
    async def load(self, extra_env=None):
        self._tmp = tempfile.TemporaryDirectory()
        env = {'DISCORD_TOKEN': 'OFFLINE_DUMMY', 'GEMINI_API_KEY': 'OFFLINE_DUMMY',
               'MUSIC_ENABLED': '0', 'MUSIC_SYNC_COMMANDS': '0', 'MEMORY_DIR': self._tmp.name}
        env.update(extra_env or {})
        with patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location('offline_bot_presence', ROOT / 'bot.py')
            m = importlib.util.module_from_spec(spec)
            load_source(spec, m)
        self.addAsyncCleanup(m.client.close)
        return m

    def guild(self, members):
        return types.SimpleNamespace(id=1, members=members)

    async def test_default_off_means_no_intent_no_context(self):
        m = await self.load()
        self.assertFalse(m.PRESENCE_ENABLED)
        self.assertFalse(m.intents.presences)
        g = self.guild([member(1, "Fazal", activity=activity())])
        self.assertEqual(m.presence_context(g, "kaun online hai?"), "")

    async def test_enabled_flag_sets_intent(self):
        m = await self.load({'PRESENCE_ENABLED': '1'})
        self.assertTrue(m.PRESENCE_ENABLED)
        self.assertTrue(m.intents.presences)

    async def test_roster_only_when_request_is_about_presence(self):
        m = await self.load({'PRESENCE_ENABLED': '1'})
        start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=95)
        g = self.guild([member(1, "Junaid", activity=activity(start=start)),
                        member(2, "AFKGuy", status="offline"),
                        member(3, "Loti", bot=True, activity=activity())])
        out = m.presence_context(g, "kaun online hai server me?")
        self.assertIn("Junaid", out)
        self.assertIn("playing Genshin Impact", out)
        self.assertIn("(1h35)", out)          # elapsed from Discord-provided start
        self.assertNotIn("AFKGuy", out)       # offline
        self.assertNotIn("Loti", out)         # bots excluded
        self.assertEqual(m.presence_context(g, "nice build bro"), "")

    async def test_named_member_scopes_snapshot(self):
        m = await self.load({'PRESENCE_ENABLED': '1'})
        g = self.guild([member(1, "Junaid", activity=activity()),
                        member(2, "Naiyo", activity=activity(name="Spotify", kind="listening"))])
        out = m.presence_context(g, "naiyo kya sun raha hai?")
        self.assertIn("Naiyo", out)
        self.assertIn("listening to Spotify", out)
        self.assertNotIn("Junaid", out)

    async def test_no_start_time_means_no_elapsed(self):
        m = await self.load({'PRESENCE_ENABLED': '1'})
        g = self.guild([member(1, "Junaid", activity=activity(start=None))])
        out = m.presence_context(g, "junaid kya khel raha hai?")
        self.assertIn("playing Genshin Impact", out)
        self.assertNotIn("min)", out)
        self.assertNotIn("h)", out)


class ProactiveRules(unittest.TestCase):
    def load(self, env=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.dict(os.environ, {'MEMORY_DIR': tmp.name, **(env or {})}):
            spec = importlib.util.spec_from_file_location('offline_proactive', ROOT / 'proactive.py')
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
        return m

    def test_help_bid_prefilter(self):
        m = self.load()
        self.assertTrue(m.looks_like_help_bid("koi hai? team bata do"))
        self.assertTrue(m.looks_like_help_bid("ye build kaisa laga review karo"))
        self.assertFalse(m.looks_like_help_bid("lol nice"))
        self.assertFalse(m.looks_like_help_bid("?"))

    def test_track_due_after_grace(self):
        m = self.load()
        t = [1000.0]
        p = m.Proactive(state=m.Proactive()._default(), now=lambda: t[0])
        self.assertTrue(p.track(10, 100, 42, "best weapon for neuvillette?"))
        self.assertEqual(len(p.due(10)), 1)  # zero grace: due at the next tick
        # the grace mechanism itself still works when configured
        m5 = self.load({'PROACTIVE_GRACE_MINUTES': '5'})
        p5 = m5.Proactive(state=m5.Proactive()._default(), now=lambda: t[0])
        t[0] += 1
        p5.track(10, 100, 42, "best weapon for neuvillette?")
        self.assertEqual(p5.due(10), [])
        t[0] += 5 * 60 + 1
        self.assertEqual(len(p5.due(10)), 1)

    def test_cancel_only_on_real_address(self):
        m = self.load()
        p = m.Proactive()
        p.track(10, 100, 42, "koi review karega?")
        # unrelated chatter from another member cancels NOTHING
        p.cancel_if_addressed(10, 77, None)
        self.assertEqual(len(p.state["pending"]), 1)
        # a reply-reference to the pending message cancels
        p.cancel_if_addressed(10, 77, 100)
        self.assertEqual(len(p.state["pending"]), 0)
        # asker's own follow-up cancels
        p.track(10, 101, 42, "aur ye wala build?")
        p.cancel_if_addressed(10, 42, None)
        self.assertEqual(len(p.state["pending"]), 0)

    def test_daily_cap_and_cooldown(self):
        m = self.load()
        t = [1000.0]
        p = m.Proactive(state=m.Proactive()._default(), now=lambda: t[0])
        for _ in range(m.DAILY_CAP):
            p.mark_posted(10, "k")
        self.assertFalse(p.allowed_today(10))   # cap hit
        p2 = m.Proactive(state=p._default(), now=lambda: t[0])
        p2.mark_posted(10, "k")
        t[0] += m.COOLDOWN_MINUTES * 60 - 1
        self.assertFalse(p2.allowed_today(10))  # cooldown
        t[0] += 2
        self.assertTrue(p2.allowed_today(10))

    def test_grace_default_is_zero(self):
        m = self.load()
        self.assertEqual(m.GRACE_MINUTES, 0)

    def test_her_direct_reply_cancels_the_askers_pending(self):
        m = self.load()
        p = m.Proactive()
        p.track(10, 100, 42, "best weapon for neuvillette?")
        p.track(10, 101, 77, "koi team review karega?")
        p.cancel_addressed_by_reply(10, 42, 100)
        remaining = list(p.state["pending"].values())
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["author_id"], 77)

    def test_opener_idle_clock(self):
        m = self.load()
        t = [1000.0]
        p = m.Proactive(state=m.Proactive()._default(), now=lambda: t[0])
        p.seed_activity(10)
        self.assertFalse(p.opener_due(10))          # just booted: never an instant opener
        t[0] += m.OPENER_IDLE_HOURS * 3600 - 1
        self.assertFalse(p.opener_due(10))          # not yet
        t[0] += 2
        self.assertTrue(p.opener_due(10))           # idle long enough
        p.note_activity(10)                          # human spoke: clock resets
        self.assertFalse(p.opener_due(10))

    def test_quiet_hours_overnight_window(self):
        m = self.load()
        late = datetime.datetime(2026, 11, 18, 23, 30, tzinfo=m.IST)
        noon = datetime.datetime(2026, 11, 18, 12, 0, tzinfo=m.IST)
        morning = datetime.datetime(2026, 11, 18, 7, 59, tzinfo=m.IST)
        self.assertTrue(m.Proactive().quiet_now(late))
        self.assertTrue(m.Proactive().quiet_now(morning))
        self.assertFalse(m.Proactive().quiet_now(noon))

    def test_resolve_channels_general_chat_only(self):
        m = self.load()
        ch = lambda cid, name: types.SimpleNamespace(id=cid, name=name)
        guild = types.SimpleNamespace(
            id=9,
            text_channels=[ch(46, "general-chat"), ch(47, "clips-highlights"), ch(48, "truth-dare")],
            get_channel=lambda cid: {46: ch(46, "general-chat"), 47: ch(47, "clips-highlights")}.get(cid))
        got = m.Proactive.resolve_channels(guild, "46", "47")
        self.assertEqual(set(got), {"general-chat"})            # clips and truth-dare are out of scope
        self.assertEqual(got["general-chat"].id, 46)
        self.assertEqual(set(m.Proactive.resolve_channels(guild, "46", None)), {"general-chat"})
        # configured chat channel missing, or no guild -> fail closed
        self.assertIsNone(m.Proactive.resolve_channels(guild, "99", "47"))
        self.assertIsNone(m.Proactive.resolve_channels(None, "46", "47"))


if __name__ == '__main__':
    unittest.main()
