"""Regression: KRISTY_ONLY_GUILD_ID must isolate a trial instance to the test
guild - messages from any other guild (or DMs) return before any processing,
so a second instance can never act in production guilds. Default (env unset)
stays fully open, which the rest of the on_message suite already proves.
"""
import importlib.util
import os
import pathlib
import tempfile
import types
import unittest
import warnings
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).parents[1]


class OnlyGuildGate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        env = {
            'DISCORD_TOKEN': 'OFFLINE_DUMMY', 'GEMINI_API_KEY': 'OFFLINE_DUMMY',
            'MUSIC_ENABLED': '0', 'MUSIC_SYNC_COMMANDS': '0',
            'KRISTY_ONLY_GUILD_ID': '555',
            'ALIASES_FILE': str(pathlib.Path(self._tmp.name) / 'aliases.json'),
        }
        with patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location('offline_bot_gate', ROOT / 'bot.py')
            self.bot = importlib.util.module_from_spec(spec)
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', category=DeprecationWarning)
                spec.loader.exec_module(self.bot)
        self.addAsyncCleanup(self.bot.client.close)

    async def test_other_guild_human_message_ignored(self):
        self.bot.record_message = MagicMock()
        msg = types.SimpleNamespace(
            author=types.SimpleNamespace(bot=False, id=1, display_name='Fazal'),
            guild=types.SimpleNamespace(id=999), webhook_id=None)
        await self.bot.on_message(msg)
        self.bot.record_message.assert_not_called()

    async def test_other_guild_bot_message_not_even_recorded(self):
        self.bot.record_message = MagicMock()
        msg = types.SimpleNamespace(
            author=types.SimpleNamespace(bot=True, id=2, display_name='Loti'),
            guild=types.SimpleNamespace(id=999), webhook_id=None)
        await self.bot.on_message(msg)
        self.bot.record_message.assert_not_called()

    async def test_dm_ignored_in_trial_mode(self):
        self.bot.record_message = MagicMock()
        msg = types.SimpleNamespace(
            author=types.SimpleNamespace(bot=False, id=1, display_name='Fazal'),
            guild=None, webhook_id=None)
        await self.bot.on_message(msg)
        self.bot.record_message.assert_not_called()


if __name__ == '__main__':
    unittest.main()
