"""Discord components-v2 cards: buttons live inside the card; the player panel is re-posted at chat bottom on every change. Callbacks revalidate guild/VC membership every time."""
import asyncio
import os
import re
import time
import discord
from .player import MusicError
from .media import MediaError
from .diagnostics import emit
from . import card_image

ERROR_TEXT = 'Music request failed. Try again later; no API keys or session details are shown.'
ACCENT = 0xB79AFF
PLAYER_FOOTER = 'VC chat only • No AI for music controls'

def clean(text, limit=180):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))[:limit]

def _icon(env_name, fallback):
    # Custom monochrome application emoji (name:id or bare id) uploaded to the bot's app; unicode fallback.
    raw = os.getenv(env_name, '').strip()
    if raw:
        parts = raw.split(':')
        if parts[-1].isdigit():
            name = parts[-2] if len(parts) > 1 else 'icon'
            return discord.PartialEmoji(name=name, id=int(parts[-1]))
    return fallback

def duration(seconds):
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    return f'{h}:{m:02}:{s:02}' if h else f'{m}:{s:02}'

async def answer(interaction, text=None, *, ephemeral=False, **kwargs):
    # Components-v2 messages cannot carry text content; omit it entirely when None.
    kwargs.setdefault('allowed_mentions', discord.AllowedMentions.none())
    if text is not None: kwargs['content'] = text
    if interaction.response.is_done():
        return await interaction.followup.send(ephemeral=ephemeral, **kwargs)
    return await interaction.response.send_message(ephemeral=ephemeral, **kwargs)

async def callback_error(interaction, exc):
    text = str(exc) if isinstance(exc, (MusicError, MediaError)) else ERROR_TEXT
    await answer(interaction, text, ephemeral=True)

def track_state(player):
    # Derived from real playback state, never from which code path posted the panel:
    # a current track whose audio has not started is Loading, not Playing.
    if player.current is None: return 'Idle'
    if not getattr(player, 'started', 0): return 'Loading audio...'
    return 'Paused' if player.paused else 'Playing'

def thumbnail_url(track):
    # Deterministic YouTube still for the video id; Discord fetches it, the bot downloads nothing.
    vid = getattr(track, 'id', '') or ''
    return f'https://i.ytimg.com/vi/{vid}/mqdefault.jpg' if re.fullmatch(r'[A-Za-z0-9_-]{11}', vid) else None

def player_text(player, status=None):
    t = player.current
    lines = ['## Kristy • Audio player']
    if t:
        lines.append(f'**{clean(t.title)}**\n{duration(t.duration)} • [YouTube]({t.url})')
        lines.append(f"**{status or track_state(player)}** • Requested by <@{t.requester}>")
    else:
        lines.append('Ready. Search YouTube or use /play with a title or video link.')
    queued = list(player.queue)[:3]
    lines.append(f"**Up next • {len(player.queue)}**\n" + ('\n'.join(clean(x.title, 90) for x in queued) or 'Queue is empty'))
    return '\n'.join(lines)

class SearchModal(discord.ui.Modal, title='Search YouTube'):
    """Opened from the player's Search button; submit runs the same path as /search."""
    query = discord.ui.TextInput(label='YouTube title, video or link', max_length=200, required=True)
    def __init__(self, handler):
        super().__init__()
        self.handler = handler
    async def on_submit(self, interaction):
        text = str(self.query.value or '').strip()
        if not text:
            await answer(interaction, 'Type a title or link to search.', ephemeral=True); return
        await self.handler(interaction, text)
    async def on_error(self, interaction, error):
        await callback_error(interaction, error)

def _search_button(manager):
    button = discord.ui.Button(label='Search', emoji=_icon('MUSIC_ICON_SEARCH', '\U0001F50E'), style=discord.ButtonStyle.secondary)
    async def on_search(interaction):
        try:
            manager.caller_channel(interaction.user, interaction.channel)   # same VC-chat rule as /search
            handler = getattr(manager, 'search_handler', None)
            if handler is None: raise MusicError('Search is not available right now')
            await interaction.response.send_modal(SearchModal(handler))      # must be the first response
        except Exception as exc: await callback_error(interaction, exc)
    button.callback = on_search
    return button

CARD_FILE = 'kristy-card.png'

_noted = set()

def _fallback_note(reason):
    # Once per process and reason; fixed words plus an exception class name, never message text.
    if reason in _noted: return
    _noted.add(reason)
    print('MUSIC_CARD fallback ' + reason, flush=True)

ART_WAIT = 1.5      # most a panel waits for artwork before posting with the placeholder
ART_TOTAL = 6       # hard cap for one whole artwork download (all hosts and transports)
ART_ATTEMPTS = 3    # downloads per track id, so a dead network is never retried on every panel

async def _art_job(player, vid):
    art = None
    try: art = await asyncio.wait_for(card_image.fetch_art(vid), ART_TOTAL)
    except asyncio.TimeoutError: card_image.art_error = 'TIMEOUT'
    except Exception as exc: card_image.art_error = type(exc).__name__
    cache = player.art_cache
    if art:
        cache[vid] = art
        while len(cache) > 6: cache.pop(next(iter(cache)))
    else:
        _fallback_note('ART_' + str(card_image.art_error or 'Unknown'))
    late = vid in player.art_late
    player.art_late.discard(vid); player.art_jobs.pop(vid, None)
    if art and late and getattr(player.current, 'id', None) == vid and not player.closed:
        await player.manager.panel(player)  # one refresh now that the artwork is here

async def _art_wait(player, vid):
    """Never blocks past ART_WAIT: a slow download continues in the background and refreshes the card once."""
    if not hasattr(player, 'art_jobs'): player.art_jobs, player.art_late, player.art_tries = {}, set(), {}
    job = player.art_jobs.get(vid)
    if job is None:
        if player.art_tries.get(vid, 0) >= ART_ATTEMPTS: return
        player.art_tries[vid] = player.art_tries.get(vid, 0) + 1
        job = player.art_jobs[vid] = asyncio.create_task(_art_job(player, vid))
    try: await asyncio.wait_for(asyncio.shield(job), ART_WAIT)
    except asyncio.TimeoutError: player.art_late.add(vid)  # still downloading: the job refreshes the card when done

async def prepare_card(player, status=None):
    """Draw the upper card block for the current state. Sets player.card_png (None means: use native layout)."""
    player.card_png = None
    t = player.current
    if not card_image.available():
        _fallback_note('NO_PILLOW'); return
    if not t:
        player.card_png = await card_image.render_block_async('Nothing playing', 'Use /play or search in this chat', None, 'Idle')
        if not player.card_png: _fallback_note('RENDER_FAILED ' + str(card_image.last_error or 'Unknown'))
        return
    cache = getattr(player, 'art_cache', None)
    if cache is None: cache = player.art_cache = {}
    if t.id not in cache: await _art_wait(player, t.id)
    state = track_state(player)
    label = status or state
    card_image.art_error = None
    player.card_png = await card_image.render_block_async(
        clean(t.title, 120), f'YouTube • {duration(t.duration)}', cache.get(t.id), label, loading=state.startswith('Loading'))
    if str(card_image.art_error or '').startswith('DECODE_') and cache.get(t.id):
        cache.pop(t.id, None); _fallback_note('ART_' + card_image.art_error)  # corrupt bytes are dropped so the next panel refetches
    if not player.card_png:
        _fallback_note('RENDER_FAILED ' + str(card_image.last_error or 'Unknown'))

class Controls(discord.ui.LayoutView):
    """Player card: text and icon-only control buttons inside one container."""
    def __init__(self, manager, player, status=None):
        super().__init__(timeout=None)
        self.manager, self.player = manager, player
        container = discord.ui.Container(accent_color=ACCENT)
        self.card_png = getattr(player, 'card_png', None)
        if self.card_png:
            t = player.current
            container.add_item(discord.ui.Section(discord.ui.TextDisplay('## ♬\u2003Music Player\n-# Kristy'), accessory=_search_button(manager)))
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem('attachment://' + CARD_FILE,
                               description=(f"{clean(t.title, 100)} - {status or track_state(player)}" if t else 'Nothing playing'))))
        else:
            head, body = player_text(player, status).split('\n', 1)   # own header Section so Search works without Pillow too
            container.add_item(discord.ui.Section(discord.ui.TextDisplay(head), accessory=_search_button(manager)))
            text = discord.ui.TextDisplay(body)
            thumb = thumbnail_url(player.current) if player.current else None
            if thumb:
                container.add_item(discord.ui.Section(text, accessory=discord.ui.Thumbnail(thumb, description='Song thumbnail')))
            else:
                container.add_item(text)
        row = discord.ui.ActionRow()
        playing = player.current and not player.paused
        buttons = [(_icon('MUSIC_ICON_PAUSE' if playing else 'MUSIC_ICON_PLAY', '⏸️' if playing else '▶️'), 'pause', discord.ButtonStyle.primary),
                   (_icon('MUSIC_ICON_SKIP', '⏭️'), 'next', discord.ButtonStyle.secondary),
                   (_icon('MUSIC_ICON_QUEUE', '📋'), 'queue', discord.ButtonStyle.secondary),
                   (_icon('MUSIC_ICON_STOP', '⏹️'), 'stop', discord.ButtonStyle.danger),
                   (_icon('MUSIC_ICON_LEAVE', '👋'), 'leave', discord.ButtonStyle.secondary)]
        for emoji, action, style in buttons:
            b = discord.ui.Button(emoji=emoji, style=style)
            async def on_click(interaction, action=action):
                try:
                    p = await self.manager.get(interaction.user, interaction.channel)
                    if p is not self.player: raise MusicError('This player is from an ended session')
                    await interaction.response.defer()
                    if action == 'pause':
                        if not await p.start_queue(): await p.pause(not p.paused)
                    elif action == 'next': await p.interrupt()
                    elif action == 'stop': await p.interrupt(clear=True)
                    elif action == 'leave': await self.manager.remove(p, 'Left VC. Queue cleared.')
                    elif action == 'queue':
                        await answer(interaction, embed=queue_embed(p))
                        await self.manager.panel(p)
                except Exception as exc: await callback_error(interaction, exc)
            b.callback = on_click
            row.add_item(b)
        controls_row = row
        toggle = discord.ui.Button(label='Autoplay: On' if player.autoplay else 'Autoplay: Off', emoji=_icon('MUSIC_ICON_AUTOPLAY', None),
                                   style=discord.ButtonStyle.success if player.autoplay else discord.ButtonStyle.secondary)
        async def on_toggle(interaction):
            try:
                p = await self.manager.get(interaction.user, interaction.channel)
                if p is not self.player: raise MusicError('This player is from an ended session')
                await interaction.response.defer()
                await p.set_autoplay(not p.autoplay)
            except Exception as exc: await callback_error(interaction, exc)
        toggle.callback = on_toggle
        if self.card_png:
            if player.current:
                t = player.current   # both lines in one text block so they sit as close as Up next / Queue is empty
                container.add_item(discord.ui.TextDisplay(f'[YouTube]({t.url}) • {duration(t.duration)}\nRequested by <@{t.requester}>'))
            autoplay_row = discord.ui.ActionRow(); autoplay_row.add_item(toggle)
            container.add_item(autoplay_row)   # its own row: the normal gap between components separates it from the requester line
            container.add_item(controls_row)
            container.add_item(discord.ui.TextDisplay(f"**Up next • {len(player.queue)}**\n" + ('\n'.join(clean(x.title, 90) for x in list(player.queue)[:3]) or 'Queue is empty')))
            self.add_item(container)
            return
        container.add_item(controls_row)
        toggle_row = discord.ui.ActionRow(); toggle_row.add_item(toggle)
        container.add_item(toggle_row)
        footer = PLAYER_FOOTER + (' • Autoplay on: related tracks play when the queue ends' if player.autoplay else '')
        container.add_item(discord.ui.TextDisplay('-# ' + footer))
        self.add_item(container)

def _card_file(self):
    import io
    return discord.File(io.BytesIO(self.card_png), filename=CARD_FILE) if self.card_png else None
Controls.card_file = _card_file

class Results(discord.ui.LayoutView):
    """Search card: numbered results with their Play/Add to queue buttons inside one container."""
    def __init__(self, manager, channel_id, tracks):
        super().__init__(timeout=180)
        self.manager, self.channel_id, self.tracks = manager, channel_id, tracks
        self.claims = {}
        self.expires = time.monotonic() + 180
        self.claim_lock = asyncio.Lock()
        container = discord.ui.Container(accent_color=ACCENT)
        container.add_item(discord.ui.TextDisplay('## Kristy • YouTube search\nAudio only. Play interrupts the current track; Add to queue only queues.'))
        for index, track in enumerate(tracks):
            container.add_item(discord.ui.TextDisplay(f'**{index+1}. {clean(track.title, 140)}**\n{duration(track.duration)} • [YouTube]({track.url})'))
            row = discord.ui.ActionRow()
            for action in ('Play', 'Add to queue'):
                button = discord.ui.Button(label=action,
                        style=discord.ButtonStyle.success if action == 'Play' else discord.ButtonStyle.secondary)
                async def on_click(interaction, index=index, action=action):
                    key = (interaction.user.id, index, action)
                    claimed = False
                    try:
                        if time.monotonic() > self.expires: raise MusicError('Search results expired; search again')
                        if interaction.channel_id != self.channel_id: raise MusicError('Use the original VC chat')
                        self.manager.caller_channel(interaction.user, interaction.channel)
                        async with self.claim_lock:
                            if time.monotonic() - self.claims.get(key, -100) < 2: raise MusicError('Request just accepted; wait a moment before pressing again')
                            self.claims[key] = time.monotonic()
                            claimed = True
                        await interaction.response.defer()
                        request_began = time.monotonic()
                        p = await self.manager.get(interaction.user, interaction.channel, join=True)
                        from dataclasses import replace
                        track = replace(self.tracks[index], requester=interaction.user.id)
                        self.manager.caller_channel(interaction.user, interaction.channel)
                        if action == 'Play':
                            was_playing = p.current is not None
                            await p.play_now(track)
                        else:
                            await p.add(track, start=False)
                        emit('REQUEST','OK',elapsed=time.monotonic()-request_began)
                        await answer(interaction, f'Added: {clean(track.title)}.' + (' Switching tracks. Loading audio...' if action == 'Play' and was_playing else (' Loading audio...' if action == 'Play' else ' Use /play to start if idle.')))
                        await self.manager.panel(p)
                    except Exception as exc:
                        if claimed:
                            async with self.claim_lock: self.claims.pop(key, None)
                        await callback_error(interaction, exc)
                button.callback = on_click
                row.add_item(button)
            container.add_item(row)
        container.add_item(discord.ui.TextDisplay('-# Results expire in 3 minutes • Controls work only for members in this VC'))
        self.add_item(container)


def queue_embed(player):
    embed = discord.Embed(title='Kristy • Queue', color=0xB79AFF)
    lines = []
    if player.current: lines.append('Now: ' + clean(player.current.title))
    for i, t in enumerate(list(player.queue)[:20]): lines.append(f'{i+1}. {clean(t.title, 95)} • {duration(t.duration)}')
    if len(player.queue) > 20: lines.append(f'...and {len(player.queue)-20} more')
    embed.description = '\n'.join(lines) or 'Queue is empty.'
    return embed


def player_panel(manager, player, status=None):
    return Controls(manager, player, status)
