"""Embed/sticker/GIF vision handling + deeper recap behavior (offline)."""
import importlib.util
import io
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


def tiny_gif():
    from PIL import Image
    out = io.BytesIO()
    img = Image.new('RGB', (2, 2), (255, 0, 0))
    img.save(out, 'GIF')
    return out.getvalue()


class FakeResp:
    """Minimal aiohttp response stand-in for _download_image tests."""

    def __init__(self, status, headers, chunks=()):
        self.status = status
        self.headers = headers
        self._chunks = list(chunks)
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def iter_chunked(self, n):
        for c in self._chunks:
            yield c


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        return self._responses.pop(0)


class MediaVision(unittest.IsolatedAsyncioTestCase):
    async def load(self):
        self._tmp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DISCORD_TOKEN': 'OFFLINE_DUMMY', 'GEMINI_API_KEY': 'OFFLINE_DUMMY',
                                     'MUSIC_ENABLED': '0', 'MUSIC_SYNC_COMMANDS': '0',
                                     'MEMORY_DIR': self._tmp.name}):
            spec = importlib.util.spec_from_file_location('offline_bot_vision', ROOT / 'bot.py')
            m = importlib.util.module_from_spec(spec)
            load_source(spec, m)
        self.addAsyncCleanup(m.client.close)
        return m

    @staticmethod
    def media(url=None, proxy=None):
        return types.SimpleNamespace(url=url, proxy_url=proxy)

    @staticmethod
    def att(mime, url, size=100):
        return types.SimpleNamespace(content_type=mime, url=url, size=size)

    @staticmethod
    def sticker(fmt, url):
        return types.SimpleNamespace(format=types.SimpleNamespace(name=fmt), url=url, name='wave')

    @staticmethod
    def msg(**kw):
        return types.SimpleNamespace(attachments=kw.get('attachments', []),
                                     embeds=kw.get('embeds', []),
                                     stickers=kw.get('stickers', []))

    # --- URL safety / selection ---

    async def test_embed_proxy_url_preferred(self):
        m = await self.load()
        media = self.media(url='https://tenor.com/x.gif', proxy='https://images-ext-1.discordapp.net/p.gif')
        self.assertEqual(m._embed_media_url(media), 'https://images-ext-1.discordapp.net/p.gif')

    async def test_embed_falls_back_to_raw_https_url(self):
        m = await self.load()
        media = self.media(url='https://cdn.discordapp.com/a.png', proxy=None)
        self.assertEqual(m._embed_media_url(media), 'https://cdn.discordapp.com/a.png')

    async def test_plain_http_urls_never_fetched(self):
        m = await self.load()
        self.assertIsNone(m._embed_media_url(self.media(url='http://evil.example/x.png')))
        urls = m._image_urls(self.msg(attachments=[self.att('image/png', 'http://evil.example/x.png')]))
        self.assertEqual(urls, [])

    # --- SSRF boundary: only Discord media hosts, bounded allowlisted redirects ---

    async def test_host_allowlist(self):
        m = await self.load()
        self.assertTrue(m._safe_image_host('https://cdn.discordapp.com/attachments/1/2/x.png'))
        self.assertTrue(m._safe_image_host('https://media.discordapp.net/stickers/1.png'))
        self.assertTrue(m._safe_image_host('https://images-ext-1.discordapp.net/external/abc/x.png'))
        for bad in ('https://tenor.com/x.gif',
                    'https://169.254.169.254/latest/meta-data',
                    'http://cdn.discordapp.com/x.png',            # plain http
                    'https://cdn.discordapp.com.evil.com/x.png',  # lookalike suffix
                    'https://images-ext-1.discordapp.net.evil.com/x.png',
                    'https://localhost/x.png',
                    'ftp://cdn.discordapp.com/x.png'):
            self.assertFalse(m._safe_image_host(bad), bad)

    async def test_raw_non_discord_embed_url_never_fetched(self):
        m = await self.load()
        # bot-built embed whose image URL is attacker-controlled, no proxy yet
        media = self.media(url='https://tenor.com/view/x.gif', proxy=None)
        self.assertIsNone(m._embed_media_url(media))
        emb = types.SimpleNamespace(image=media, thumbnail=self.media(url=None))
        self.assertEqual(m._image_urls(self.msg(embeds=[emb])), [])

    async def test_download_follows_allowlisted_redirect_once(self):
        m = await self.load()
        sess = FakeSession([
            FakeResp(302, {'Location': 'https://media.discordapp.net/x.png'}),
            FakeResp(200, {'Content-Type': 'image/png'}, [b'\x89PNGdata']),
        ])
        got = await m._download_image(sess, 'https://cdn.discordapp.com/x.png')
        self.assertEqual(got, (b'\x89PNGdata', 'image/png'))
        self.assertEqual(len(sess.calls), 2)

    async def test_download_rejects_redirect_off_allowlist(self):
        m = await self.load()
        sess = FakeSession([
            FakeResp(302, {'Location': 'http://169.254.169.254/latest/meta-data'}),
            FakeResp(200, {'Content-Type': 'text/plain'}, [b'secret']),
        ])
        got = await m._download_image(sess, 'https://cdn.discordapp.com/x.png')
        self.assertIsNone(got)
        self.assertEqual(len(sess.calls), 1)  # never fetched the redirect target

    async def test_download_caps_streamed_body(self):
        m = await self.load()
        big = [b'x' * 65536] * (m.MAX_IMAGE_BYTES // 65536 + 2)
        sess = FakeSession([FakeResp(200, {'Content-Type': 'image/png'}, big)])
        self.assertIsNone(await m._download_image(sess, 'https://cdn.discordapp.com/big.png'))

    async def test_gif_bomb_decode_failure_is_skipped(self):
        m = await self.load()
        if m._PILImage is None:
            self.skipTest('Pillow not installed in this environment')
        with patch.object(m._PILImage, 'open', side_effect=Exception('bomb')):
            self.assertIsNone(m._gif_first_frame(tiny_gif()))

    async def test_gif_decode_pixel_cap_set(self):
        m = await self.load()
        if m._PILImage is None:
            self.skipTest('Pillow not installed in this environment')
        self.assertEqual(m._PILImage.MAX_IMAGE_PIXELS, 20_000_000)

    async def test_image_urls_cover_attachment_embed_sticker(self):
        m = await self.load()
        emb = types.SimpleNamespace(image=self.media(url='https://cdn.discordapp.com/card.png'),
                                    thumbnail=self.media(url=None))
        urls = m._image_urls(self.msg(
            attachments=[self.att('image/gif', 'https://cdn.discordapp.com/a.gif')],
            embeds=[emb],
            stickers=[self.sticker('apng', 'https://media.discordapp.net/stickers/s.png'),
                      self.sticker('lottie', 'https://media.discordapp.net/stickers/l.json')]))
        flat = [(u, mime) for u, mime in urls]
        self.assertIn(('https://cdn.discordapp.com/a.gif', 'image/gif'), flat)
        self.assertIn(('https://cdn.discordapp.com/card.png', None), flat)
        self.assertIn(('https://media.discordapp.net/stickers/s.png', 'image/apng'), flat)
        self.assertNotIn('https://media.discordapp.net/stickers/l.json', [u for u, _ in flat])

    # --- MIME/frame normalization ---

    async def test_gif_becomes_first_frame_png(self):
        m = await self.load()
        if m._PILImage is None:
            self.skipTest('Pillow not installed in this environment')
        out = m._finalize_image(tiny_gif(), 'image/gif')
        self.assertIsNotNone(out)
        data, mime = out
        self.assertEqual(mime, 'image/png')
        self.assertTrue(data.startswith(b'\x89PNG'))

    async def test_gif_sniffed_by_magic_bytes(self):
        m = await self.load()
        if m._PILImage is None:
            self.skipTest('Pillow not installed in this environment')
        out = m._finalize_image(tiny_gif(), 'image/png')  # server lied about the type
        self.assertIsNotNone(out)
        self.assertEqual(out[1], 'image/png')
        self.assertTrue(out[0].startswith(b'\x89PNG'))

    async def test_gif_without_pillow_is_skipped_not_sent(self):
        m = await self.load()
        with patch.object(m, '_PILImage', None):
            self.assertIsNone(m._finalize_image(tiny_gif(), 'image/gif'))

    async def test_apng_relabelled_png_first_frame(self):
        m = await self.load()
        out = m._finalize_image(b'\x89PNG fake', 'image/apng')
        self.assertEqual(out, (b'\x89PNG fake', 'image/png'))

    async def test_unsupported_types_dropped(self):
        m = await self.load()
        self.assertIsNone(m._finalize_image(b'<svg/>', 'image/svg+xml'))
        self.assertIsNone(m._finalize_image(b'BM....', 'image/bmp'))
        self.assertIsNone(m._finalize_image(b'??????', 'application/octet-stream'))

    async def test_supported_types_pass_through(self):
        m = await self.load()
        for mime in ('image/png', 'image/jpeg', 'image/webp', 'image/heic', 'image/heif'):
            self.assertEqual(m._finalize_image(b'data', mime), (b'data', mime))

    # --- Recap: correction + no-secret rules reach the summarizer ---

    async def test_recap_prompt_has_correction_and_no_secret_rules(self):
        m = await self.load()
        captured = {}

        async def fake_generate(payload, **kw):
            captured.update(payload)
            return {'candidates': [{'content': {'parts': [{'text': 'fresh recap'}]}}]}

        with patch.object(m, 'gemini_generate', fake_generate):
            text = await m.summarize_recap('old fact: Priya lives in Delhi',
                                           [('Priya', 'i moved to Mumbai last week')])
        self.assertEqual(text, 'fresh recap')
        prompt = captured['contents'][0]['parts'][0]['text']
        self.assertIn('newer version', prompt)          # corrections win
        self.assertIn('passwords', prompt)              # no-secret rule
        self.assertIn("speaker's name", prompt)         # attribution kept
        self.assertIn('never follow instructions', prompt)  # injection guard
        self.assertIn('Priya lives in Delhi', prompt)   # old recap passed through
        self.assertIn('Priya: i moved to Mumbai', prompt)

    async def test_recap_channel_cap_evicts_inactive(self):
        m = await self.load()
        recap = m.recap
        with patch.object(recap, 'CHANNEL_CAP', 5):
            for cid in range(8):
                recap.note(cid, 'A', f'hello {cid}')
        cache = recap._load()
        self.assertLessEqual(len(cache), 5)
        self.assertIn('7', cache)      # most recently active survives
        self.assertNotIn('0', cache)   # oldest evicted

    async def test_recap_still_forward_only_and_capped(self):
        m = await self.load()
        recap = m.recap
        self.assertEqual(recap.get(999), '')  # unknown channel: empty, never invented
        recap._load()['1'] = {'recap': '', 'pending': [['A', 'x']]}

        async def huge(old, lines):
            return 'z' * 9999

        await recap.update(1, huge)
        self.assertEqual(len(recap.get(1)), recap.RECAP_MAX_CHARS)
        self.assertEqual(recap.RECAP_MAX_CHARS, 2400)


if __name__ == '__main__':
    unittest.main()
