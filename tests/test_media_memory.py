"""Bot-context media + rolling recap integration in bot.py (offline)."""
import asyncio
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


class BotMediaMemory(unittest.IsolatedAsyncioTestCase):
    async def load(self):
        self._tmp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DISCORD_TOKEN': 'OFFLINE_DUMMY', 'GEMINI_API_KEY': 'OFFLINE_DUMMY',
                                     'MUSIC_ENABLED': '0', 'MUSIC_SYNC_COMMANDS': '0',
                                     'MEMORY_DIR': self._tmp.name}):
            spec = importlib.util.spec_from_file_location('offline_bot_media', ROOT / 'bot.py')
            m = importlib.util.module_from_spec(spec)
            load_source(spec, m)
        self.addAsyncCleanup(m.client.close)
        return m

    @staticmethod
    def embed(**kw):
        ns = types.SimpleNamespace()
        ns.author = types.SimpleNamespace(name=kw.get('author', ''))
        ns.title = kw.get('title', '')
        ns.description = kw.get('description', '')
        ns.image = types.SimpleNamespace(url=kw.get('image'))
        ns.thumbnail = types.SimpleNamespace(url=kw.get('thumbnail'))
        ns.video = types.SimpleNamespace(url=kw.get('video'))
        return ns

    @staticmethod
    def msg(**kw):
        ns = types.SimpleNamespace()
        ns.content = kw.get('content', '')
        ns.embeds = kw.get('embeds', [])
        ns.attachments = kw.get('attachments', [])
        ns.stickers = kw.get('stickers', [])
        author = types.SimpleNamespace(display_name=kw.get('name', 'Junaid'),
                                       bot=kw.get('bot', False), id=kw.get('uid', 42))
        ns.author = author
        ns.channel = types.SimpleNamespace(id=555)
        ns.guild = types.SimpleNamespace(id=99)
        ns.reference = kw.get('reference')
        ns.message_snapshots = kw.get('snapshots', [])
        ns.webhook_id = kw.get('webhook_id')
        return ns

    async def test_describe_embeds_bot_avatar_card(self):
        m = await self.load()
        emb = self.embed(author="JAYRAJ's avatar", image='https://cdn.discordapp.com/a.png')
        note = m.describe_embeds(self.msg(embeds=[emb]))
        self.assertIn("JAYRAJ's avatar", note)
        self.assertIn('[image]', note)

    async def test_record_foreign_marks_bot_and_keeps_embed(self):
        m = await self.load()
        emb = self.embed(title="JAYRAJ's avatar", image='https://x/y.png')
        m.record_foreign(self.msg(name='Loti', bot=True, content='', embeds=[emb]))
        author, _aid, text = m.history[555][-1]
        self.assertEqual(author, 'Loti (bot)')
        self.assertIn("JAYRAJ's avatar", text)

    async def test_history_overflow_feeds_recap_buffer(self):
        m = await self.load()
        m._schedule_recap = lambda channel_id: None  # no real AI call in tests
        for i in range(m.HISTORY_LEN + m.recap.RECAP_EVERY):
            m.record_message(555, 'Junaid', 42, f'msg {i}')
        self.assertEqual(len(m.history[555]), m.HISTORY_LEN)
        self.assertEqual(len(m.recap._load()['555']['pending']), m.recap.RECAP_EVERY)
        # oldest lines are what got buffered, in order
        self.assertEqual(m.recap._load()['555']['pending'][0][1], 'msg 0')

    async def test_recap_reaches_system_context(self):
        m = await self.load()
        m.recap._load()['555'] = {'recap': 'Junaid pulled Furina at P78.', 'pending': []}
        note = m.channel_context_note(555)
        self.assertIn('OLDER CHAT RECAP', note)
        self.assertIn('P78', note)
        self.assertEqual(m.channel_context_note(777), '')

    async def test_resolve_reference_forwarded_snapshot(self):
        m = await self.load()
        snap = self.msg(content='forwarded hello', name='Mayank')
        target = await m.resolve_reference(self.msg(reference=None, snapshots=[snap]))
        self.assertIs(target, snap)

    async def test_describe_message_note_marks_bot(self):
        m = await self.load()
        emb = self.embed(description='some card', thumbnail='https://x/t.png')
        name, text = m.describe_message_note(self.msg(name='Loti', bot=True, embeds=[emb]))
        self.assertEqual(name, 'Loti (bot)')
        self.assertIn('[embed:', text)
        self.assertIn('[image]', text)

    async def test_reply_hint_keeps_speakers_untangled(self):
        m = await self.load()
        junaid_msg = self.msg(content='kal tournament hai', name='Junaid', uid=1)
        with patch.object(m.discord, 'Message', types.SimpleNamespace):
            reply = self.msg(content='haan sahi hai', name='Mayank', uid=2,
                             reference=types.SimpleNamespace(resolved=junaid_msg))
            hint = m.reply_hint(reply)
            self_hint = m.reply_hint(self.msg(content='x', reference=None))
        self.assertIn('Junaid', hint)
        self.assertIn('tournament', hint)
        self.assertEqual(self_hint, '')

    async def test_interleaved_speakers_recorded_with_authors_and_hints(self):
        m = await self.load()
        m._schedule_recap = lambda channel_id: None
        msg_a = self.msg(content='pehla match kab hai', name='Junaid', uid=1)
        m.record_message(555, msg_a.author.display_name, msg_a.author.id, m.stored_text(msg_a, msg_a.content))
        msg_b = self.msg(content='raat ko hoga shayad', name='Mayank', uid=2)
        m.record_message(555, msg_b.author.display_name, msg_b.author.id, m.stored_text(msg_b, msg_b.content))
        with patch.object(m.discord, 'Message', types.SimpleNamespace):
            reply = self.msg(content='Kristy ye kis baat ka jawab tha', name='Junaid', uid=1,
                             reference=types.SimpleNamespace(resolved=msg_b))
            stored = f"{m.reply_hint(reply)} {m.stored_text(reply, reply.content)}".strip()[:300]
        m.record_message(555, reply.author.display_name, reply.author.id, stored)
        authors = [a for a, _i, _t in m.history[555]]
        self.assertEqual(authors, ['Junaid', 'Mayank', 'Junaid'])
        self.assertIn('[replying to Mayank:', m.history[555][-1][2])

    async def test_channel_locks_serialize_overlapping_requests(self):
        m = await self.load()
        order = []

        async def request(name, delay):
            async with m._channel_locks[555]:
                order.append(f'{name}-start')
                await asyncio.sleep(delay)
                order.append(f'{name}-end')

        await asyncio.gather(request('a', 0.05), request('b', 0.01))
        self.assertEqual(order, ['a-start', 'a-end', 'b-start', 'b-end'])
        self.assertIs(m._channel_locks[555], m._channel_locks[555])
        self.assertIsNot(m._channel_locks[555], m._channel_locks[777])

    async def test_source_scope_current_request_marker_and_lock(self):
        src = (ROOT / 'bot.py').read_text()
        self.assertIn('CURRENT REQUEST:', src)
        self.assertIn('never answer someone else\'s pending question', src)
        self.assertIn('_channel_locks[message.channel.id]', src)
        self.assertIn('lock_held.release()', src)

    async def test_stored_text_keeps_media_markers(self):
        m = await self.load()
        att = types.SimpleNamespace(content_type='image/gif', filename='dance.gif', size=100, url='https://x/g.gif')
        text = m.stored_text(self.msg(content='dekh ye', attachments=[att]), 'dekh ye')
        self.assertIn('[image]', text)


if __name__ == '__main__':
    unittest.main()
