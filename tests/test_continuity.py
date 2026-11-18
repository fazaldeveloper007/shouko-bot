"""Conversation-continuation rules (offline, no discord import)."""
import importlib.util
import os
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).parents[1]


def load(env=None):
    with patch.dict(os.environ, env or {}):
        spec = importlib.util.spec_from_file_location('offline_continuity', ROOT / 'continuity.py')
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module=m)
    return m


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Rules(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.clock = FakeClock()
        self.c = self.mod.Continuity(clock=self.clock, window_minutes=4, enabled=True)

    def test_question_reply_arms_window_and_answer_is_accepted(self):
        self.c.note_bot_reply(1, 42, "Kaisa chal raha hai?")
        self.assertTrue(self.c.is_continuation(1, 42))

    def test_plain_reply_arms_nothing(self):
        self.c.note_bot_reply(1, 42, "Badhiya, mast ekdum!")
        self.assertFalse(self.c.is_continuation(1, 42))

    def test_question_with_trailing_emoji_still_arms(self):
        self.c.note_bot_reply(1, 42, "Kya hua? 🥺")
        self.assertTrue(self.c.is_continuation(1, 42))

    def test_expired_window_is_rejected(self):
        self.c.note_bot_reply(1, 42, "Kaisi ho?")
        self.clock.advance(4 * 60 + 1)
        self.assertFalse(self.c.is_continuation(1, 42))

    def test_mention_of_someone_else_is_never_a_continuation(self):
        self.c.note_bot_reply(1, 42, "Kaisi ho?")
        self.assertFalse(self.c.is_continuation(1, 42, mentions_other=True))

    def test_native_reply_to_another_message_is_never_a_continuation(self):
        self.c.note_bot_reply(1, 42, "Kaisi ho?")
        self.assertFalse(self.c.is_continuation(1, 42, has_reference=True))

    def test_interleaved_human_chat_kills_the_window(self):
        self.c.note_bot_reply(1, 42, "Kaisi ho?")
        self.c.note_interruption(1)  # someone else said something in between
        self.assertFalse(self.c.is_continuation(1, 42))

    def test_direct_trigger_by_another_member_kills_the_window(self):
        self.c.note_bot_reply(1, 42, "Kaisi ho?")
        self.c.note_direct_trigger(1, 99)  # member 99 name-drops Kristy
        self.assertFalse(self.c.is_continuation(1, 42))

    def test_accepted_window_is_consumed_not_self_sustaining(self):
        self.c.note_bot_reply(1, 42, "Kaisi ho?")
        self.assertTrue(self.c.is_continuation(1, 42))
        # no new question from her: the same window cannot chain
        self.assertFalse(self.c.is_continuation(1, 42))

    def test_concurrent_members_keep_separate_windows(self):
        self.c.note_bot_reply(1, 42, "Tumhara build kaisa hai?")
        self.c.note_bot_reply(1, 99, "Aur tera?")  # her spotlight moved to 99
        self.assertFalse(self.c.is_continuation(1, 42))  # 42's window is over
        self.assertTrue(self.c.is_continuation(1, 99))

    def test_her_questionless_reply_to_someone_else_ends_the_window(self):
        self.c.note_bot_reply(1, 42, "Kaisi ho?")
        self.c.note_bot_reply(1, 99, "haha sahi hai")  # no question, other member
        self.assertFalse(self.c.is_continuation(1, 42))
        self.assertFalse(self.c.is_continuation(1, 99))

    def test_channels_are_independent(self):
        self.c.note_bot_reply(1, 42, "Kaisi ho?")
        self.c.note_interruption(2)  # noise in another channel changes nothing
        self.assertTrue(self.c.is_continuation(1, 42))

    def test_disabled_means_no_continuations(self):
        c = self.mod.Continuity(clock=self.clock, window_minutes=4, enabled=False)
        c.note_bot_reply(1, 42, "Kaisi ho?")
        self.assertFalse(c.is_continuation(1, 42))


class EnvDefaults(unittest.TestCase):
    def test_default_on_with_four_minute_window(self):
        for k in ('CONTINUITY_ENABLED', 'CONTINUITY_MINUTES'):
            os.environ.pop(k, None)
        m = load()
        self.assertTrue(m.ENABLED)
        self.assertEqual(m.WINDOW_MINUTES, 4.0)

    def test_env_kill_switch(self):
        m = load({'CONTINUITY_ENABLED': '0'})
        self.assertFalse(m.ENABLED)


if __name__ == '__main__':
    unittest.main()
