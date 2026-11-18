"""Search-routing classification and per-route generation counts (offline)."""
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
            spec = importlib.util.spec_from_file_location('offline_bot_route', ROOT / 'bot.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_bot()

    def test_hinglish_current_events_route_directly(self):
        # Sanctioned categories (versions/patches/banners/updates/news/leaks)
        # the system prompt already tells the model to search: routing them
        # directly skips only the [[search:]] marker round-trip.
        for text in (
            "abhi kaun sa banner chal raha hai",
            "abhi konsa banner hai",
            "meta kya chal raha hai",
            "abyss ka current lineup kya hai",
            "5.8 mein kya kya aa raha hai",
            "drip marketing aayi kya",
        ):
            self.assertTrue(self.m.looks_like_search(text), text)

    def test_casual_chat_stays_off_search(self):
        for text in (
            "kaisi ho",
            "kya kar rahi ho",
            "mujhe neuvillette bohot pasand hai",
            "kal exam hai wish karo",
            "banner pe pity lost ho gaya yaar",
            "main abhi banner nahi kheench raha",
            "new phone le liya maine",
            "current affairs padh raha tha",
            "best movie batao koi",
        ):
            self.assertFalse(self.m.looks_like_search(text), text)

    def test_first_person_status_is_not_a_forced_search(self):
        self.assertFalse(self.m.looks_like_search("build kar raha hu apna hutao"))
        # but the same words as a real question still route
        self.assertTrue(self.m.looks_like_search("build kar raha hu hutao ka, best artifacts bata?"))

    def test_existing_explicit_hits_unchanged(self):
        for text in (
            "search karo best build for neuvillette",
            "pata karo next patch kab hai",
            "current banners batao",
            "naya update kab aayega",
            "aane wala banner kiska hai",
            "livestream kab hai next",
            "best build for mavuika",
        ):
            self.assertTrue(self.m.looks_like_search(text), text)


class CallCounts(unittest.IsolatedAsyncioTestCase):
    """Explicit route costs 1 generation; the marker route costs 2."""

    @classmethod
    def setUpClass(cls):
        cls.m = load_bot()

    async def test_explicit_route_one_generation(self):
        m = self.m
        calls = []

        async def fake_think(*a, **k):
            calls.append("think")
            return "grounded answer"

        async def fake_fetch(query):
            return "quick answer: x\n- t: snippet (https://example.com)"

        m.history[9].append(("Riya", 42, "current banners batao"))
        with patch.object(m, "think", fake_think), \
             patch.object(m, "fetch_search_results", fake_fetch):
            reply = await m.think_search(9, "", requester_id=42)
        self.assertEqual(reply, "grounded answer")
        self.assertEqual(calls, ["think"])  # exactly ONE generation

    async def test_marker_route_two_generations(self):
        m = self.m
        calls = []

        async def fake_think(*a, **k):
            calls.append("think")
            return "grounded answer"

        async def fake_fetch(query):
            return "quick answer: x\n- t: snippet (https://example.com)"

        m.history[11].append(("Riya", 42, "abhi kaun sa banner chal raha hai"))
        with patch.object(m, "think", fake_think), \
             patch.object(m, "fetch_search_results", fake_fetch):
            first = await fake_think(11)  # upstream generation that emits the marker
            self.assertEqual(calls, ["think"])
            reply = await m.maybe_model_search(11, "[[search: current banners]]", "")
        self.assertEqual(reply, "grounded answer")
        self.assertEqual(calls, ["think", "think"])  # TWO generations total


if __name__ == "__main__":
    unittest.main()
