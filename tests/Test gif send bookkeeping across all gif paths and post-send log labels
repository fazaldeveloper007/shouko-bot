"""Regression: on_message crashed AFTER delivering a gif reply with
UnboundLocalError: gif_char_key, because only the AI-marker branch assigned
it while every gif branch reached the _last_gif_sent bookkeeping. The broad
except then logged a misleading "Failed to send reply" for a reply that was
already out. All gif paths (direct request, mention ride, mood category,
AI marker) and the plain non-gif path must send cleanly, and only character
gifs get recorded as a "send that one" marker target.
"""
import importlib.util
import os
import pathlib
import tempfile
import types
import unittest
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = pathlib.Path(__file__).parents[1]


def member(mid, name, display=None, bot=False):
    return types.SimpleNamespace(
        id=mid, name=name, display_name=display or name,
        global_name=None, bot=bot, mention=f"<@{mid}>",
    )


class GifSendRecord(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        env = {
            'DISCORD_TOKEN': 'OFFLINE_DUMMY', 'GEMINI_API_KEY': 'OFFLINE_DUMMY',
            'MUSIC_ENABLED': '0', 'MUSIC_SYNC_COMMANDS': '0',
            'ALIASES_FILE': str(pathlib.Path(self._tmp.name) / 'aliases.json'),
        }
        with patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location('offline_bot_gifrec', ROOT / 'bot.py')
            self.bot = importlib.util.module_from_spec(spec)
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', category=DeprecationWarning)
                spec.loader.exec_module(self.bot)
        self.bot.aliases._ALIASES_FILE = pathlib.Path(self._tmp.name) / 'aliases.json'
        self.bot.self_diary.PATH = pathlib.Path(self._tmp.name) / 'lessons.json'
        self.addAsyncCleanup(self.bot.client.close)
        self.fazal = member(1, 'fazal_techiudex07', 'Fazal')
        self.me = member(99, 'kristy_bot', 'Kristy', bot=True)
        self.guild = types.SimpleNamespace(
            id=555, name='Gaming Impact', members=[self.fazal, self.me], me=self.me)
        self.guild.get_member = lambda uid: next((m for m in self.guild.members if m.id == uid), None)
        self.bot.client.music = None
        self.bot._last_gif_sent.pop(555000, None)

    def msg(self, content):
        m = types.SimpleNamespace(
            guild=self.guild, author=self.fazal, content=content,
            mentions=[], channel_mentions=[], attachments=[], stickers=[],
            embeds=[], reference=None, id=100,
            channel=types.SimpleNamespace(id=555000, name='general', send=AsyncMock()),
            reply=AsyncMock(),
        )
        m.channel.typing = MagicMock()
        m.channel.typing.return_value.__aenter__ = AsyncMock()
        m.channel.typing.return_value.__aexit__ = AsyncMock()
        m.reply.return_value = types.SimpleNamespace(id=900)
        return m

    async def run_msg(self, content, plan=None, brain_text=None):
        m = self.msg(content)
        patches = [
            patch.object(self.bot, 'try_reaction', new_callable=AsyncMock),
            patch.object(self.bot, 'maybe_schedule_extraction'),
        ]
        if plan is not None:
            patches.append(patch.object(self.bot, 'plan_gif', return_value=plan))
        if brain_text is not None:
            patches.append(patch.object(self.bot, 'think', new_callable=AsyncMock,
                                        return_value=brain_text))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        records = []
        handler = __import__('logging').Handler()
        handler.emit = records.append
        self.bot.log.addHandler(handler)
        try:
            await self.bot.on_message(m)
        finally:
            self.bot.log.removeHandler(handler)
        return m, "\n".join(r.getMessage() for r in records if r.levelno >= 30)

    async def test_direct_request_records_character_key(self):
        m, logs = await self.run_msg('Kristy neuvillette gif bhejo',
                                     plan=("request", "neuvillette"))
        m.reply.assert_awaited_once()
        gif_sends = [c for c in m.channel.send.await_args_list
                     if 'http' in str(c)]
        self.assertTrue(gif_sends)
        key, _ts = self.bot._last_gif_sent[555000]
        self.assertEqual(key, 'neuvillette')
        self.assertNotIn('Failed to send reply', logs)
        self.assertNotIn('post-send tracking failed', logs)

    async def test_mention_ride_records_character_key(self):
        m, logs = await self.run_msg('Kristy furina is my favourite, honestly',
                                     plan=("mention", "furina"),
                                     brain_text='she has great taste')
        m.reply.assert_awaited_once()
        key, _ts = self.bot._last_gif_sent[555000]
        self.assertEqual(key, 'furina')
        self.assertNotIn('Failed to send reply', logs)
        self.assertNotIn('post-send tracking failed', logs)

    async def test_mood_category_sends_without_marker_record(self):
        m, logs = await self.run_msg('Kristy good morning everyone',
                                     plan=("category", "good_morning"),
                                     brain_text='good morning!')
        m.reply.assert_awaited_once()
        self.assertNotIn(555000, self.bot._last_gif_sent)
        self.assertNotIn('Failed to send reply', logs)
        self.assertNotIn('post-send tracking failed', logs)

    async def test_ai_marker_path_records_marker_key(self):
        m, logs = await self.run_msg('Kristy send a neuvillette gif please',
                                     plan=("none", None),
                                     brain_text='here you go [[gif:neuvillette]]')
        m.reply.assert_awaited_once()
        key, _ts = self.bot._last_gif_sent[555000]
        self.assertEqual(key, 'neuvillette')
        self.assertNotIn('Failed to send reply', logs)
        self.assertNotIn('post-send tracking failed', logs)

    async def test_plain_reply_no_gif_no_record(self):
        m, logs = await self.run_msg('kya haal hai Kristy',
                                     plan=("none", None),
                                     brain_text='sab badhiya, tum sunao')
        m.reply.assert_awaited_once()
        m.channel.send.assert_not_awaited()
        self.assertNotIn(555000, self.bot._last_gif_sent)
        self.assertNotIn('Failed to send reply', logs)
        self.assertNotIn('post-send tracking failed', logs)

    async def test_post_send_failure_never_logged_as_send_failure(self):
        m = self.msg('Kristy neuvillette gif bhejo')
        with patch.object(self.bot, 'plan_gif', return_value=("request", "neuvillette")), \
             patch.object(self.bot, 'maybe_schedule_extraction'), \
             patch.object(self.bot, 'try_reaction', new_callable=AsyncMock,
                          side_effect=RuntimeError('boom')):
            records = []
            handler = __import__('logging').Handler()
            handler.emit = records.append
            self.bot.log.addHandler(handler)
            try:
                await self.bot.on_message(m)
            finally:
                self.bot.log.removeHandler(handler)
        out = "\n".join(r.getMessage() for r in records if r.levelno >= 30)
        self.assertIn('post-send tracking failed', out)
        self.assertNotIn('Failed to send reply', out)
        m.reply.assert_awaited_once()  # the reply itself was delivered


if __name__ == '__main__':
    unittest.main()
