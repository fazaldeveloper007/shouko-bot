"""Offline integration smoke tests. Dummy config; never client.run/start/sync."""
import asyncio
import inspect
import importlib.util
import os
import pathlib
import types
import unittest
import warnings

def load_source(spec, module):
    # discord.py2.7.1 event decorator uses a Python3.14-deprecated alias.
    # Limit this exception to that exact dependency call during offline import.
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message=r"^'asyncio\.iscoroutinefunction' is deprecated and slated for removal in Python 3\.16; use inspect\.iscoroutinefunction\(\) instead$", category=DeprecationWarning, module=r'^discord\.client$')
        spec.loader.exec_module(module)

from unittest.mock import patch, AsyncMock

ROOT=pathlib.Path(__file__).parents[1]
class BotIntegration(unittest.IsolatedAsyncioTestCase):
    async def load(self, enabled):
        with patch.dict(os.environ,{'DISCORD_TOKEN':'OFFLINE_DUMMY','GEMINI_API_KEY':'OFFLINE_DUMMY','MUSIC_ENABLED':str(enabled),'MUSIC_SYNC_COMMANDS':'0'}):
            spec=importlib.util.spec_from_file_location('offline_bot_'+str(enabled),ROOT/'bot.py');m=importlib.util.module_from_spec(spec);load_source(spec,m)
        self.addAsyncCleanup(m.client.close)
        return m
    async def test_python314_dependency_warning_precisely_scoped(self):
        def deprecated(obj):
            warnings.warn("'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead",DeprecationWarning,stacklevel=2)
            return inspect.iscoroutinefunction(obj)
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            with patch('asyncio.iscoroutinefunction',deprecated):
                await self.load(0)
                await self.load(1)
            with self.assertRaises(DeprecationWarning):
                warnings.warn('unrelated compatibility regression',DeprecationWarning)
            with self.assertRaises(DeprecationWarning):
                deprecated(None)  # Same text outside discord.client remains fatal.

    async def test_disabled_preserves_existing_helpers(self):
        m=await self.load(0);self.assertIsNone(m.client.music)
        self.assertTrue(m.looks_like_search('search latest genshin codes'))
        self.assertGreater(len(m.chunk_reply('word '*1500)),1)
        note=m.describe_stickers(types.SimpleNamespace(stickers=[types.SimpleNamespace(name='Paimon',format=types.SimpleNamespace(name='png'))]))
        self.assertIn('Paimon',note)
    async def test_enabled_reuses_same_client_no_login_no_sync(self):
        m=await self.load(1)
        self.assertIs(m.client.music.client,m.client)
        self.assertIs(m.client.music.manager.client,m.client)
        self.assertEqual(len(m.client.music.tree.get_commands()),8)
        with patch.object(m.client.music.tree,'sync',new=AsyncMock()) as sync:
            with patch.dict(os.environ,{'MUSIC_SYNC_COMMANDS':'0'}):await m.client.music.ready()
            sync.assert_not_awaited()
    async def test_source_scope_no_music_details_in_diaries(self):
        s=(ROOT/'bot.py').read_text()
        self.assertIn('client.music.context(message.channel)',s)
        self.assertNotIn('and not (client.music and isinstance(message.channel, discord.VoiceChannel))',s)
        self.assertIn('durable_exchange(speaker, content',s)

if __name__=='__main__':unittest.main()
