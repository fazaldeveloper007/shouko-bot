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
            secs = [c for c in comps if c['type'] == 9]
            self.assertEqual(secs[0]['accessory']['label'], 'Search')   # header Section, button at the right
            self.assertIn('Music Player', secs[0]['components'][0]['content'])
            sec = secs[1]
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
        self.assertIn('MUSIC_CARD fallback RENDER_FAILED ValueError', buf.getvalue())
    async def test_no_pillow_means_native_fallback(self):
        p = self.player(); p.current = track()
        with patch.object(card_image, 'available', return_value=False):
            await ui.prepare_card(p)
        self.assertIsNone(p.card_png)
    async def test_header_has_space_between_note_and_title(self):
        p = self.player(); p.current = track(); await ui.prepare_card(p)
        self.assertIn('\u266c\u2003Music Player', self.texts(ui.player_panel(p.manager, p)))
    async def test_idle_keeps_card_design_no_old_header_or_footer(self):
        p = self.player(); await ui.prepare_card(p)
        self.assertTrue(p.card_png)
        text = self.texts(ui.player_panel(p.manager, p))
        self.assertIn('Music Player', text); self.assertNotIn('Audio player', text); self.assertNotIn('VC chat only', text)
        self.assertIn('Up next', text)
    async def test_failed_art_is_not_cached_so_playing_retries(self):
        p = self.player(); p.current = track(); p.started = 0
        f = AsyncMock(side_effect=[None, jpeg()])
        with patch.object(card_image, 'fetch_art', f):
            await ui.prepare_card(p); loading = p.card_png
            p.started = 1; await ui.prepare_card(p)
        self.assertEqual(f.await_count, 2)
        self.assertEqual(Image.open(io.BytesIO(loading)).convert('RGB').getpixel((10, 160)), (128, 128, 128))
        self.assertNotEqual(Image.open(io.BytesIO(p.card_png)).convert('RGB').getpixel((10, 160)), (128, 128, 128))
    async def test_fetch_falls_back_to_second_transport_and_reports_reason(self):
        with patch.object(card_image, '_fetch_aiohttp', AsyncMock(side_effect=OSError())), \
             patch.object(card_image, '_fetch_sync', lambda url: b'x'):
            self.assertEqual(await card_image.fetch_art('abcdefghijk'), b'x')
        with patch.object(card_image, '_fetch_aiohttp', AsyncMock(side_effect=OSError())), \
             patch.object(card_image, '_fetch_sync', lambda url: (_ for _ in ()).throw(ValueError())):
            self.assertIsNone(await card_image.fetch_art('abcdefghijk'))
        self.assertEqual(card_image.art_error, 'ValueError')
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

class SearchButtonTests(Base):
    def button(self, p):
        view = ui.player_panel(p.manager, p)
        return [c for c in view.walk_children() if getattr(c, 'label', None) == 'Search'][0]
    def inter(self, vc_member=True, channel_id=20):
        resp = types.SimpleNamespace(send_modal=AsyncMock(), is_done=lambda: False, send_message=AsyncMock())
        user = types.SimpleNamespace(id=7, voice=types.SimpleNamespace(channel=self.vc) if vc_member else None)
        return types.SimpleNamespace(user=user, channel=types.SimpleNamespace(id=channel_id), response=resp, followup=types.SimpleNamespace(send=AsyncMock()))
    async def test_search_button_in_header_opens_modal_first(self):
        p = self.player(); p.current = track(); p.card_png = b'x'
        self.vc = p.channel
        with patch.object(ui.discord, 'VoiceChannel', types.SimpleNamespace):
            p.manager.search_handler = AsyncMock()
            i = self.inter(); i.user.voice.channel = types.SimpleNamespace(id=20, guild=p.channel.guild)
            await self.button(p).callback(i)
        i.response.send_modal.assert_awaited_once()
        self.assertIsInstance(i.response.send_modal.await_args.args[0], ui.SearchModal)
    async def test_modal_submit_runs_same_search_handler_with_query(self):
        handler = AsyncMock(); modal = ui.SearchModal(handler)
        modal.query._value = '  slowed luz roja '
        await modal.on_submit(types.SimpleNamespace())
        handler.assert_awaited_once(); self.assertEqual(handler.await_args.args[1], 'slowed luz roja')
    async def test_blank_query_is_refused_without_search(self):
        handler = AsyncMock(); modal = ui.SearchModal(handler); modal.query._value = '   '
        i = types.SimpleNamespace(response=types.SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))
        await modal.on_submit(i)
        handler.assert_not_awaited(); i.response.send_message.assert_awaited_once()
    async def test_wrong_chat_or_no_vc_gets_same_error_and_no_modal(self):
        p = self.player(); p.current = track(); p.card_png = b'x'; self.vc = p.channel
        p.manager.search_handler = AsyncMock()
        i = self.inter(vc_member=False)
        await self.button(p).callback(i)
        i.response.send_modal.assert_not_awaited(); i.response.send_message.assert_awaited_once()
        self.assertIn('voice channel', i.response.send_message.await_args.kwargs.get('content', ''))
    async def test_idle_card_has_search_button_too(self):
        p = self.player(); await ui.prepare_card(p)
        self.assertEqual(self.button(p).label, 'Search')
    def test_integration_search_handler_calls_existing_search(self):
        import inspect
        from music import integration
        self.assertIn("'search', query", inspect.getsource(integration.Music.__init__))

class FinalPolishTests(Base):
    def blue(self, png):
        im = Image.open(io.BytesIO(png)).convert('RGB'); w, h = im.size
        return sum(1 for x in range(330, w) for y in range(h) if im.getpixel((x, y))[2] > 220 and im.getpixel((x, y))[0] < 160)
    def test_source_and_duration_not_drawn_in_image_but_state_is(self):
        png = card_image.render_block('Song', 'YouTube • 3:04', None, 'Playing')
        self.assertEqual(self.blue(png), 0)
        self.assertEqual(self.blue(card_image.render_block('Song', 'YouTube • 3:04', None, 'Loading audio...', loading=True)), 0)
    def test_title_is_bold_even_without_a_bold_font(self):
        def dark_ink(png):
            im = Image.open(io.BytesIO(png)).convert('L'); return sum(1 for x in range(340, 900) for y in range(20, 90) if im.getpixel((x, y)) > 200)
        bold = card_image.render_block('Bold title', '', None, 'Playing')
        with patch.object(card_image, 'BOLD_FONTS', []):
            faux = card_image.render_block('Bold title', '', None, 'Playing')
            font, stroke = card_image._title_font(42)
        self.assertGreater(stroke, 0); self.assertTrue(faux)
        with patch.object(card_image, 'BOLD_FONTS', []), patch.object(card_image, '_title_font', lambda size: (card_image._font(size, False), 0)):
            plain = card_image.render_block('Bold title', '', None, 'Playing')
        self.assertGreater(dark_ink(faux), dark_ink(plain) * 1.1)

class TitleWeightTests(Base):
    def stub(self, axes):
        calls = []
        class F:
            def get_variation_axes(self): return axes
            def set_variation_by_axes(self, v): calls.append(v)
        return F(), calls
    def run_title(self, axes):
        from PIL import ImageFont
        font, calls = self.stub(axes)
        font.getmask = lambda text: [1] * (100 if calls else 50)   # heavier once the weight axis was set
        def fake(path, size):
            if path in card_image.BOLD_FONTS: raise OSError()
            return font
        with patch.object(ImageFont, 'truetype', fake):
            return card_image._title_font(42), calls
    def test_variable_font_with_weight_axis_is_set_to_700_without_stroke(self):
        (f, stroke), calls = self.run_title([{'name': b'Weight', 'minimum': 100, 'maximum': 900, 'default': 400}, {'name': b'Width', 'minimum': 75, 'maximum': 100, 'default': 100}])
        self.assertEqual(stroke, 0); self.assertEqual(calls, [[700, 100]])
    def test_unidentified_axis_or_regular_only_face_gets_visible_stroke(self):
        (f, stroke), calls = self.run_title([{'name': b'Width', 'minimum': 75, 'maximum': 100, 'default': 100}])
        self.assertEqual(stroke, card_image.FAUX_STROKE); self.assertEqual(calls, [])
        (f, stroke), calls = self.run_title([])
        self.assertEqual(stroke, card_image.FAUX_STROKE)

class TitleWeightNoEffectTests(TitleWeightTests):
    def test_weight_axis_that_changes_nothing_still_gets_stroke(self):
        from PIL import ImageFont
        font, calls = self.stub([{'name': b'Weight', 'minimum': 100, 'maximum': 900, 'default': 400}])
        font.getmask = lambda text: [1] * 50                      # no heavier after the axis call
        def fake(path, size):
            if path in card_image.BOLD_FONTS: raise OSError()
            return font
        with patch.object(ImageFont, 'truetype', fake):
            f, stroke = card_image._title_font(42)
        self.assertEqual(stroke, card_image.FAUX_STROKE)

class ArtDownloadTests(Base):
    async def test_body_arriving_in_pieces_is_read_completely(self):
        from aiohttp import web
        body = jpeg(480, 360)
        async def handler(request):
            r = web.StreamResponse(headers={'Content-Length': str(len(body))}); await r.prepare(request)
            for i in range(0, len(body), 500):
                await r.write(body[i:i + 500]); await asyncio.sleep(0.01)   # pieces arrive over time
            return r
        app = web.Application(); app.router.add_get('/vi/{v}/x.jpg', handler)
        runner = web.AppRunner(app); await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            with patch.object(card_image, 'ART_URLS', ['http://127.0.0.1:%d/vi/%%s/x.jpg' % port]):
                data = await card_image.fetch_art('abcdefghijk')
        finally: await runner.cleanup()
        self.assertEqual(data, body)
        card_image.art_error = None
        self.assertTrue(card_image.render_block('t', 'm', data, 'Playing')); self.assertIsNone(card_image.art_error)
    async def test_corrupt_art_is_logged_and_dropped_not_cached(self):
        import contextlib
        p = self.player(); p.current = track(); p.started = 1; ui._noted.clear()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), patch.object(card_image, 'fetch_art', AsyncMock(return_value=jpeg()[:300])):
            await ui.prepare_card(p)
        self.assertTrue(p.card_png); self.assertNotIn(p.current.id, p.art_cache)
        self.assertIn('MUSIC_CARD fallback ART_DECODE_', buf.getvalue())

class ArtTimingTests(Base):
    async def test_slow_art_does_not_block_and_refreshes_card_once(self):
        p = self.player(); p.current = track(); p.started = 0
        gate = asyncio.Event()
        async def slow(vid):
            await gate.wait(); return jpeg()
        with patch.object(card_image, 'fetch_art', slow), patch.object(ui, 'ART_WAIT', 0.05):
            began = asyncio.get_running_loop().time()
            await ui.prepare_card(p)
            self.assertLess(asyncio.get_running_loop().time() - began, 0.5)
            self.assertTrue(p.card_png)                       # posted with the placeholder, not stalled
            p.manager.panel.assert_not_awaited()
            gate.set(); await asyncio.sleep(0.05)
        p.manager.panel.assert_awaited_once_with(p)           # single refresh when art arrived
        self.assertIn(p.current.id, p.art_cache)
    async def test_fast_art_gives_no_extra_refresh(self):
        p = self.player(); p.current = track(); p.started = 0
        with patch.object(card_image, 'fetch_art', AsyncMock(return_value=jpeg())):
            await ui.prepare_card(p); await asyncio.sleep(0.02)
        p.manager.panel.assert_not_awaited()
    async def test_whole_download_is_capped_and_attempts_limited(self):
        p = self.player(); p.current = track(); p.started = 0; ui._noted.clear()
        async def hang(vid): await asyncio.sleep(30)
        with patch.object(card_image, 'fetch_art', hang), patch.object(ui, 'ART_WAIT', 0.01), patch.object(ui, 'ART_TOTAL', 0.05):
            for _ in range(6):
                await ui.prepare_card(p); await asyncio.sleep(0.08)
        self.assertEqual(p.art_tries[p.current.id], ui.ART_ATTEMPTS)
        self.assertIn('ART_TIMEOUT', ui._noted); self.assertFalse(p.art_jobs)
    async def test_interrupt_reply_does_not_claim_playing(self):
        import inspect
        self.assertNotIn('Playing now', inspect.getsource(ui))

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
