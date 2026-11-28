import asyncio, io, sys, types, unittest
from unittest.mock import AsyncMock, patch
sys.path.insert(0, '.')
import discord
from PIL import Image
from music import card_image, ui
from music.player import GuildPlayer, Manager
from music.media import Track

def track(vid='jNQXAC9IVRw', title='Lyrical: Chammak Challo | Ra One', requester=7):
    return Track(vid, title, 246, requester)

def jpeg(w=480, h=360):
    img = Image.new('RGB', (w, h), (0, 0, 0)); img.paste((200, 60, 60), (0, h // 8, w, h - h // 8))
    out = io.BytesIO(); img.save(out, 'JPEG'); return out.getvalue()

def vc():
    return types.SimpleNamespace(id=20, guild=types.SimpleNamespace(id=10), send=AsyncMock(), members=[])

class Base(unittest.IsolatedAsyncioTestCase):
    def player(self):
        m = Manager(types.SimpleNamespace()); m.note = AsyncMock(); m.panel = AsyncMock()
        p = GuildPlayer(m, vc()); p.bridge = types.SimpleNamespace(stop_track=AsyncMock(), pause=AsyncMock(return_value=True), disconnect=AsyncMock(), transitioning=False)
        self.addCleanup(p.temp.cleanup); return p

class RenderTests(Base):
    def test_png_valid_size_and_loading_differs_from_playing(self):
        a = card_image.render_block('Song', 'YouTube • 4:06', jpeg(), 'Loading audio...', loading=True)
        b = card_image.render_block('Song', 'YouTube • 4:06', jpeg(), 'Playing')
        self.assertEqual(Image.open(io.BytesIO(a)).size, card_image.SIZE)
        self.assertNotEqual(a, b)
    def test_art_square_left_and_placeholder_when_missing_or_corrupt(self):
        art = Image.open(io.BytesIO(card_image.render_block('S', 'm', jpeg(), 'Playing'))).convert('RGB')
        self.assertGreater(art.getpixel((100, 160))[0], 150)  # artwork red, not tile gray
        for bad in (None, b'not an image'):
            png = card_image.render_block('S', 'm', bad, 'Loading audio...', loading=True)
            im = Image.open(io.BytesIO(png)).convert('RGB')
            self.assertEqual(im.getpixel((10, 160)), (128, 128, 128))  # placeholder tile
    def test_long_and_unbroken_titles_do_not_crash(self):
        for t in ('word ' * 80, 'A' * 300, 'हिंदी गाना 日本語 🎵'):
            self.assertTrue(card_image.render_block(t, 'm', jpeg(), 'Playing'))
    def test_missing_pillow_gives_none(self):
        art = jpeg()
        with patch.dict(sys.modules, {'PIL': None, 'PIL.Image': None, 'PIL.ImageDraw': None, 'PIL.ImageFont': None}):
            self.assertIsNone(card_image.render_block('S', 'm', art, 'Playing'))
            self.assertFalse(card_image.available())
    def test_video_id_validation(self):
        self.assertTrue(card_image.valid_id('jNQXAC9IVRw'))
        for bad in ('', 'short', 'x' * 12, '../../etc/pw', 'jNQXAC9IVR/'): self.assertFalse(card_image.valid_id(bad))
    async def test_fetch_rejects_bad_id_without_network(self):
        self.assertIsNone(await card_image.fetch_art('bad id'))

class CardTests(Base):
    def texts(self, view):
        out = []
        def walk(c):
            if c.get('type') == 10: out.append(c['content'])
            for x in c.get('components', []): walk(x)
            if c.get('accessory'): walk(c['accessory'])
        for c in view.to_components(): walk(c)
        return '\n'.join(out)
    async def test_prepare_then_card_layout_loading_and_playing(self):
        p = self.player(); p.current = track(); p.started = 0
        with patch.object(card_image, 'fetch_art', AsyncMock(return_value=jpeg())) as f:
            await ui.prepare_card(p)
            loading_png = p.card_png
            view = ui.player_panel(p.manager, p)
            comps = view.to_components()[0]['components']
            types_ = [c['type'] for c in comps]
            self.assertIn(12, types_)                                   # media gallery with the block image
            gallery = [c for c in comps if c['type'] == 12][0]['items'][0]
            self.assertEqual(gallery['media']['url'], 'attachment://' + ui.CARD_FILE)
            self.assertIn('Loading audio', gallery['description'])
            self.assertEqual(self.texts(view).count('[YouTube](https://www.youtube.com/watch?v=jNQXAC9IVRw)'), 1)  # clickable source line
            sec = [c for c in comps if c['type'] == 9][0]
            self.assertIn('Requested by <@7>', sec['components'][0]['content'])
            self.assertEqual(sec['accessory']['label'], 'Autoplay: Off')   # autoplay button beside requester
            rows = [c for c in comps if c['type'] == 1]
            self.assertEqual(len(rows), 1); self.assertEqual(len(rows[0]['components']), 5)
            self.assertIn('Up next', self.texts(view)); self.assertIn('Queue is empty', self.texts(view))
            self.assertIsNotNone(view.card_file())
            p.started = 12.0
            await ui.prepare_card(p)
            self.assertNotEqual(p.card_png, loading_png)
            self.assertEqual(f.await_count, 1)                           # art cached across state changes
            self.assertIn('Playing', [c for c in ui.player_panel(p.manager, p).to_components()[0]['components'] if c['type'] == 12][0]['items'][0]['description'])
    async def test_autoplay_button_still_works_inside_section(self):
        p = self.player(); p.current = track(); p.started = 5; p.card_png = b'x'
        p.set_autoplay = AsyncMock()
        p.manager.get = AsyncMock(return_value=p)
        view = ui.player_panel(p.manager, p)
        btn = [i for i in view.walk_children() if isinstance(i, discord.ui.Button) and i.label and i.label.startswith('Autoplay')][0]
        inter = types.SimpleNamespace(user=types.SimpleNamespace(id=7), channel=p.channel, response=types.SimpleNamespace(defer=AsyncMock(), is_done=lambda: True), followup=types.SimpleNamespace(send=AsyncMock()))
        await btn.callback(inter)
        p.set_autoplay.assert_awaited_once_with(True)
    async def test_fetch_failure_still_gives_card_with_placeholder_and_state(self):
        p = self.player(); p.current = track(); p.started = 0
        with patch.object(card_image, 'fetch_art', AsyncMock(return_value=None)):
            await ui.prepare_card(p)
        self.assertTrue(p.card_png)
        self.assertEqual(Image.open(io.BytesIO(p.card_png)).convert('RGB').getpixel((10, 160)), (128, 128, 128))
    async def test_render_failure_falls_back_to_native_layout_with_controls(self):
        p = self.player(); p.current = track(); p.started = 0
        with patch.object(card_image, 'fetch_art', AsyncMock(return_value=jpeg())), patch.object(card_image, 'render_block', return_value=None):
            await ui.prepare_card(p)
        self.assertIsNone(p.card_png)
        view = ui.player_panel(p.manager, p)
        self.assertIsNone(view.card_file())
        self.assertIn('Loading audio', self.texts(view))
        rows = [c for c in view.to_components()[0]['components'] if c['type'] == 1]
        self.assertEqual(len(rows[0]['components']), 5)
    async def test_fallback_reason_is_logged_once_without_message_text(self):
        import contextlib
        ui._noted.clear()
        buf = io.StringIO()
        p = self.player(); p.current = track()
        with contextlib.redirect_stdout(buf), patch.object(card_image, 'available', return_value=False):
            await ui.prepare_card(p); await ui.prepare_card(p)
        self.assertEqual(buf.getvalue().strip(), 'MUSIC_CARD fallback NO_PILLOW')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), patch.object(card_image, 'fetch_art', AsyncMock(return_value=None)), \
             patch.object(card_image, 'render_block', return_value=None), patch.object(card_image, 'last_error', 'ValueError'):
            await ui.prepare_card(p)
        self.assertEqual(buf.getvalue().strip(), 'MUSIC_CARD fallback RENDER_FAILED ValueError')
    async def test_no_pillow_means_native_fallback(self):
        p = self.player(); p.current = track()
        with patch.object(card_image, 'available', return_value=False):
            await ui.prepare_card(p)
        self.assertIsNone(p.card_png)
    async def test_idle_has_no_image(self):
        p = self.player(); await ui.prepare_card(p)
        self.assertIsNone(p.card_png); self.assertIn('Ready.', self.texts(ui.player_panel(p.manager, p)))
    async def test_art_cache_bounded(self):
        p = self.player()
        with patch.object(card_image, 'fetch_art', AsyncMock(return_value=jpeg())):
            for i in range(12):
                p.current = track(vid='%011d' % i); await ui.prepare_card(p)
        self.assertLessEqual(len(p.art_cache), 6)
    async def test_component_budget(self):
        p = self.player(); p.current = track(); p.card_png = b'x'
        view = ui.player_panel(p.manager, p)
        self.assertLessEqual(sum(1 for _ in view.walk_children()) + 1, 40)

class PanelLoopTests(Base):
    async def test_panel_loop_prepares_then_sends_file_and_survives_prepare_error(self):
        m = Manager(types.SimpleNamespace()); m.note = AsyncMock(); m.panel_interval = 0
        sent = []
        async def send(**kw):
            sent.append(kw); return types.SimpleNamespace(delete=AsyncMock())
        p = GuildPlayer(m, types.SimpleNamespace(id=20, guild=types.SimpleNamespace(id=10), send=send, members=[]))
        self.addCleanup(p.temp.cleanup)
        order = []
        async def prep(player, status): order.append('prep'); player.card_png = b'png'
        m.panel_prepare = prep
        class V:
            def __init__(s, png): s.png = png
            def stop(s): pass
            def card_file(s): return discord.File(io.BytesIO(s.png), filename='kristy-card.png') if s.png else None
        m.panel_builder = lambda pl, st: (order.append('build'), V(pl.card_png))[1]
        await m.panel(p); await asyncio.wait_for(p.panel_task, 2)
        self.assertEqual(order, ['prep', 'build']); self.assertEqual(len(sent[0]['files']), 1)
        async def boom(player, status): raise RuntimeError('x')
        m.panel_prepare = boom
        await m.panel(p); await asyncio.wait_for(p.panel_task, 2)
        self.assertNotIn('files', sent[1])      # prepare failure -> plain native panel, no crash

if __name__ == '__main__':
    unittest.main()
