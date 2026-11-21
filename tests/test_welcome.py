"""New-member welcome: live join path, same-day catch-up and dedupe."""
import asyncio
import datetime
import unittest
from unittest.mock import patch

import announcements

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
UTC = datetime.timezone.utc
# Fixed "now" for every test: 2026-11-21 16:40 IST.
NOW_LOCAL = datetime.datetime(2026, 11, 21, 16, 40, tzinfo=IST)


class FakeMember:
    def __init__(self, user_id, guild, *, bot=False, joined_at=None):
        self.id = user_id
        self.bot = bot
        self.guild = guild
        self.mention = f"<@{user_id}>"
        self.joined_at = joined_at

    def __str__(self):
        return f"member-{self.id}"


class FakeGuild:
    def __init__(self, guild_id, members=()):
        self.id = guild_id
        self.name = f"guild-{guild_id}"
        self._members = list(members)

    async def fetch_members(self, limit=None):
        for m in self._members:
            yield m


class FakeMessage:
    def __init__(self, author, content, mentions=()):
        self.author = author
        self.content = content
        self.mentions = list(mentions)


class FakeChannel:
    def __init__(self, guild, history=(), history_error=None):
        self.guild = guild
        self.sent = []
        self._history = list(history)
        self._history_error = history_error

    async def send(self, text, allowed_mentions=None):
        self.sent.append(text)
        return FakeMessage(None, text)

    async def history(self, limit=200, after=None):
        if self._history_error is not None:
            raise self._history_error
        for m in self._history:
            yield m


class FakeClient:
    def __init__(self):
        self.user = FakeMember(999999, None)


def joined(y, mo, d, h, mi, tz=UTC):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=tz)


class WelcomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        announcements._state = announcements._default_state()
        self.client = FakeClient()
        self.guild = FakeGuild(1438772090130923584)
        self.channel = FakeChannel(self.guild)
        patches = [
            patch.object(announcements, "client_ref", self.client),
            patch.object(announcements, "_now_local", lambda tz_name=None: NOW_LOCAL),
            patch.object(announcements, "save_state", lambda: None),
            patch.object(announcements, "DRY_RUN", False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.get_channel = patch.object(
            announcements, "_get_channel",
            lambda channel_id, label: self._channel())
        self.get_channel.start()
        self.addCleanup(self.get_channel.stop)

    async def _channel(self):
        return self.channel

    def member(self, user_id, **kw):
        return FakeMember(user_id, self.guild, **kw)

    async def test_live_join_sends_and_marks(self):
        m = self.member(42, joined_at=joined(2026, 11, 21, 10, 0))
        await announcements._on_member_join(m)
        self.assertEqual(len(self.channel.sent), 1)
        self.assertIn("<@42>", self.channel.sent[0])
        self.assertIn(42, announcements._state["welcomed"]["ids"])

    async def test_live_join_dedupes_same_day(self):
        m = self.member(42, joined_at=joined(2026, 11, 21, 10, 0))
        await announcements._on_member_join(m)
        await announcements._on_member_join(m)  # rejoin same day
        self.assertEqual(len(self.channel.sent), 1)

    async def test_live_join_skips_bots(self):
        await announcements._on_member_join(self.member(43, bot=True))
        self.assertEqual(self.channel.sent, [])

    async def test_live_join_skips_other_guild(self):
        other = FakeGuild(1217387325193982053)
        m = FakeMember(44, other, joined_at=joined(2026, 11, 21, 10, 0))
        await announcements._on_member_join(m)
        self.assertEqual(self.channel.sent, [])

    async def test_catchup_welcomes_same_day_joiner(self):
        m = self.member(50, joined_at=joined(2026, 11, 21, 9, 0))  # 14:30 IST, today
        self.guild._members.append(m)
        await announcements._welcome_catchup()
        self.assertEqual(len(self.channel.sent), 1)
        self.assertIn(50, announcements._state["welcomed"]["ids"])

    async def test_catchup_ignores_earlier_days(self):
        # 2026-11-20 18:29 UTC = 23:59 IST on the 20th: yesterday, must not post.
        old = self.member(51, joined_at=joined(2026, 11, 20, 18, 29))
        # 2026-11-21 00:00 IST exactly = start of today: included.
        edge = self.member(52, joined_at=joined(2026, 11, 20, 18, 30))
        self.guild._members += [old, edge]
        await announcements._welcome_catchup()
        self.assertEqual(len(self.channel.sent), 1)
        self.assertIn("<@52>", self.channel.sent[0])

    async def test_catchup_skips_history_welcomed(self):
        m = self.member(53, joined_at=joined(2026, 11, 21, 8, 0))
        self.guild._members.append(m)
        prior = FakeMessage(self.client.user, "Welcome to **guild** <@53>! 💙", [m])
        self.channel._history.append(prior)
        await announcements._welcome_catchup()
        self.assertEqual(self.channel.sent, [])

    async def test_catchup_aborts_when_history_fails(self):
        m = self.member(54, joined_at=joined(2026, 11, 21, 8, 0))
        self.guild._members.append(m)
        self.channel._history_error = RuntimeError("no access")
        await announcements._welcome_catchup()
        self.assertEqual(self.channel.sent, [])  # no duplicate risk taken

    async def test_catchup_skips_state_welcomed(self):
        m = self.member(55, joined_at=joined(2026, 11, 21, 8, 0))
        self.guild._members.append(m)
        announcements._mark_welcomed(55)
        await announcements._welcome_catchup()
        self.assertEqual(self.channel.sent, [])

    async def test_concurrent_live_joins_welcome_once(self):
        m = self.member(60, joined_at=joined(2026, 11, 21, 10, 0))
        await asyncio.gather(announcements._on_member_join(m),
                             announcements._on_member_join(m))
        self.assertEqual(len(self.channel.sent), 1)

    async def test_live_join_during_catchup_welcomes_once(self):
        m = self.member(61, joined_at=joined(2026, 11, 21, 10, 0))
        self.guild._members.append(m)
        await asyncio.gather(announcements._welcome_catchup(),
                             announcements._on_member_join(m))
        self.assertEqual(len(self.channel.sent), 1)

    async def test_state_resets_on_new_day(self):
        announcements._state["welcomed"] = {"date": "2026-11-20", "ids": [42]}
        self.assertEqual(announcements._welcomed_today(), set())

    async def test_dry_run_sends_nothing_and_marks_nothing(self):
        with patch.object(announcements, "DRY_RUN", True):
            m = self.member(56, joined_at=joined(2026, 11, 21, 9, 0))
            await announcements._on_member_join(m)
        self.assertEqual(self.channel.sent, [])
        self.assertEqual(announcements._welcomed_today(), set())


if __name__ == "__main__":
    unittest.main()
