"""Upper block of the player card (artwork left, title/source/state right) drawn as one image.
Pillow is optional: without it, or when anything here fails, the card falls back to native components."""
import asyncio
import io
import os
import re

ART_URL = 'https://i.ytimg.com/vi/%s/hqdefault.jpg'
ART_LIMIT = 1_000_000
ART_TIMEOUT = 5
SIZE = (960, 320)
last_error = None  # exception CLASS NAME of the latest render failure (never message text)
BG = (52, 54, 60)
FONT_ENV = 'MUSIC_FONT'
BOLD_FONTS = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/system/fonts/Roboto-Bold.ttf',
              '/system/fonts/NotoSans-Bold.ttf', '/system/fonts/DroidSans-Bold.ttf']
REG_FONTS = ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/system/fonts/Roboto-Regular.ttf',
             '/system/fonts/NotoSans-Regular.ttf', '/system/fonts/DroidSans.ttf']

def available():
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False

def valid_id(video_id):
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id or ''))

ART_URLS = ['https://i.ytimg.com/vi/%s/hqdefault.jpg', 'https://i.ytimg.com/vi/%s/mqdefault.jpg', 'https://img.youtube.com/vi/%s/hqdefault.jpg']
art_error = None  # fixed reason word or exception class name of the latest artwork failure (never message text)

def _fetch_sync(url):
    import urllib.request
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=ART_TIMEOUT) as r:
        data = r.read(ART_LIMIT + 1)
        expected = r.headers.get('Content-Length')
    if expected and expected.isdigit() and len(data) != int(expected): raise RuntimeError('SHORT_BODY')
    return data if 0 < len(data) <= ART_LIMIT else None

async def _fetch_aiohttp(url):
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=ART_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise RuntimeError('HTTP_%s' % response.status)
            # StreamReader.read(n) returns whatever has arrived, not the whole body: read to EOF, capped.
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > ART_LIMIT: return None
                chunks.append(chunk)
            data = b''.join(chunks)
            if response.content_length is not None and len(data) != response.content_length:
                raise RuntimeError('SHORT_BODY')
            return data if data else None

async def fetch_art(video_id):
    """Download the YouTube still ourselves (fixed hosts, size and time capped). None on any failure; art_error says why."""
    global art_error
    if not valid_id(video_id):
        art_error = 'BAD_ID'; return None
    for template in ART_URLS:
        url = template % video_id
        for name, call in (('aiohttp', lambda u: _fetch_aiohttp(u)), ('urllib', lambda u: asyncio.to_thread(_fetch_sync, u))):
            try:
                data = await call(url)
                if data: return data
                art_error = 'EMPTY'
            except Exception as exc:
                art_error = str(exc) if str(exc).startswith('HTTP_') else type(exc).__name__
    return None

def _font(size, bold):
    from PIL import ImageFont
    env = os.getenv(FONT_ENV, '').strip()
    for path in ([env] if env else []) + (BOLD_FONTS if bold else REG_FONTS):
        try: return ImageFont.truetype(path, size)
        except Exception: continue
    try: return ImageFont.load_default(size)
    except Exception: return ImageFont.load_default()

def _title_font(size):
    """(font, stroke): real bold file if present, else a variable font at weight 700, else the regular font with a stroke
    so the title is bold even on phones that only ship a regular Roboto."""
    from PIL import ImageFont
    env = os.getenv(FONT_ENV, '').strip()
    for path in ([env] if env else []) + BOLD_FONTS:
        try: return ImageFont.truetype(path, size), 0
        except Exception: continue
    for path in ([env] if env else []) + REG_FONTS:
        try: font = ImageFont.truetype(path, size)
        except Exception: continue
        try:
            font.set_variation_by_axes([700]); return font, 0
        except Exception: return font, 2
    return _font(size, False), 2

def _wrap(draw, text, font, width, lines):
    words, out, line = str(text).split(), [], ''
    for word in words:
        trial = (line + ' ' + word).strip()
        if draw.textlength(trial, font=font) <= width: line = trial; continue
        if line: out.append(line)
        line = word
        while draw.textlength(line, font=font) > width and len(line) > 1:
            cut = len(line) - 1
            while cut > 1 and draw.textlength(line[:cut], font=font) > width: cut -= 1
            out.append(line[:cut]); line = line[cut:]
    if line: out.append(line)
    if len(out) > lines:
        out = out[:lines]; last = out[-1]
        while last and draw.textlength(last + '…', font=font) > width: last = last[:-1]
        out[-1] = last + '…'
    return out

def _art_square(data, side):
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert('RGB')
    w, h = img.size
    if w >= h * 1.25:  # 16:9 still padded to 4:3: drop the black bars first
        pad = int(h * 0.125) if abs(w / h - 4 / 3) < .05 else 0
        img = img.crop((0, pad, w, h - pad)); w, h = img.size
    s = min(w, h); left, top = (w - s) // 2, (h - s) // 2
    return img.crop((left, top, left + s, top + s)).resize((side, side))

def _placeholder(draw, side):
    draw.rounded_rectangle([0, 0, side, side], 28, fill=(128, 128, 128))
    cx = cy = side / 2
    draw.rounded_rectangle([cx - 70, cy - 50, cx + 70, cy + 50], 10, outline='white', width=9)
    draw.polygon([(cx - 50, cy + 38), (cx - 12, cy - 8), (cx + 12, cy + 18), (cx + 30, cy + 2), (cx + 52, cy + 38)], fill='white')

def render_block(title, meta, art, label, loading=False):
    """PNG bytes, or None when Pillow is missing or drawing fails."""
    try:
        from PIL import Image, ImageDraw
        w, h = SIZE
        img = Image.new('RGB', SIZE, BG); draw = ImageDraw.Draw(img)
        side = h
        art_img = None
        if art:
            try: art_img = _art_square(art, side)
            except Exception as exc:
                global art_error
                art_error = 'DECODE_' + type(exc).__name__; art_img = None
        if art_img is not None:
            mask = Image.new('L', (side, side), 0); ImageDraw.Draw(mask).rounded_rectangle([0, 0, side, side], 28, fill=255)
            img.paste(art_img, (0, 0), mask)
        else:
            _placeholder(draw, side)
        x = side + 36; width = w - x - 28
        (tf, stroke), lf = _title_font(42), _font(30, True)
        lines = _wrap(draw, title, tf, width, 3) if not stroke else _wrap(draw, title, tf, width - 4, 3)
        y = 28
        for line in lines:
            draw.text((x, y), line, font=tf, fill='white', stroke_width=stroke, stroke_fill='white'); y += 54
        draw.text((x, h - 64), label, font=lf, fill=(255, 196, 80) if loading else (200, 200, 205))
        out = io.BytesIO(); img.save(out, 'PNG', optimize=True)
        return out.getvalue()
    except Exception as exc:
        global last_error
        last_error = type(exc).__name__
        return None

async def render_block_async(*args, **kwargs):
    return await asyncio.to_thread(render_block, *args, **kwargs)
