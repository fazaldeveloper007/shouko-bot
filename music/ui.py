"""Discord components-v2 cards: buttons live inside the card; the player panel is re-posted at chat bottom on every change. Callbacks revalidate guild/VC membership every time."""
import asyncio
import os
import time
import discord
from .player import MusicError
from .media import MediaError
from .diagnostics import emit

ERROR_TEXT = 'Music request failed. Try again later; no API keys or session details are shown.'
ACCENT = 0xB79AFF
PLAYER_FOOTER = 'VC chat only • No AI for music controls • 5-minute idle leave'

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

def player_text(player, status=None):
    t = player.current
    lines = ['## Kristy • Audio player']
    if t:
        lines.append(f'**{clean(t.title)}**\n{duration(t.duration)} • [YouTube]({t.url})')
        lines.append(f"**{status or ('Paused' if player.paused else 'Playing')}** • Requested by <@{t.requester}>")
    else:
        lines.append('Ready. Search YouTube or use /play with a title or video link.')
    queued = list(player.queue)[:3]
    lines.append(f"**Up next • {len(player.queue)}**\n" + ('\n'.join(clean(x.title, 90) for x in queued) or 'Queue is empty'))
    return '\n'.join(lines)

class Controls(discord.ui.LayoutView):
    """Player card: text and icon-only control buttons inside one container."""
    def __init__(self, manager, player, status=None):
        super().__init__(timeout=None)
        self.manager, self.player = manager, player
        container = discord.ui.Container(accent_color=ACCENT)
        container.add_item(discord.ui.TextDisplay(player_text(player, status)))
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
        container.add_item(row)
        toggle_row = discord.ui.ActionRow()
        toggle = discord.ui.Button(label='Autoplay: On' if player.autoplay else 'Autoplay: Off',
                                   style=discord.ButtonStyle.success if player.autoplay else discord.ButtonStyle.secondary)
        async def on_toggle(interaction):
            try:
                p = await self.manager.get(interaction.user, interaction.channel)
                if p is not self.player: raise MusicError('This player is from an ended session')
                await interaction.response.defer()
                await p.set_autoplay(not p.autoplay)
            except Exception as exc: await callback_error(interaction, exc)
        toggle.callback = on_toggle
        toggle_row.add_item(toggle)
        container.add_item(toggle_row)
        footer = PLAYER_FOOTER + (' • Autoplay on: related tracks play when the queue ends' if player.autoplay else '')
        container.add_item(discord.ui.TextDisplay('-# ' + footer))
        self.add_item(container)

class Results(discord.ui.LayoutView):
    """Search card: numbered results with their Play/Add to queue buttons inside one container."""
    def __init__(self, manager, channel_id, tracks):
        super().__init__(timeout=180)
        self.manager, self.channel_id, self.tracks = manager, channel_id, tracks
        self.claims = {}
        self.expires = time.monotonic() + 180
        self.claim_lock = asyncio.Lock()
        container = discord.ui.Container(accent_color=ACCENT)
        container.add_item(discord.ui.TextDisplay('## Kristy • YouTube search\nAudio only. Play starts when idle; Add to queue only queues.'))
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
                        await p.add(track, start=action == 'Play')
                        emit('REQUEST','OK',elapsed=time.monotonic()-request_began)
                        await answer(interaction, f'Added: {clean(track.title)}.' + (' Preparing audio now; playback starts after extraction/download.' if action == 'Play' and not p.started else (' Waiting behind the current track.' if action == 'Play' else ' Use /play to start if idle.')))
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
