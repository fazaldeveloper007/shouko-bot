"""Discord native embeds/buttons. Callbacks revalidate guild/VC membership every time."""
import asyncio
import time
import discord
from .player import MusicError
from .media import MediaError
from .diagnostics import emit

ERROR_TEXT = 'Music request failed. Try again later; no API keys or session details are shown.'

def clean(text, limit=180):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))[:limit]

def duration(seconds):
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    return f'{h}:{m:02}:{s:02}' if h else f'{m}:{s:02}'

async def answer(interaction, text, *, ephemeral=False, **kwargs):
    if interaction.response.is_done():
        return await interaction.followup.send(text, ephemeral=ephemeral, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
    return await interaction.response.send_message(text, ephemeral=ephemeral, allowed_mentions=discord.AllowedMentions.none(), **kwargs)

async def callback_error(interaction, exc):
    text = str(exc) if isinstance(exc, (MusicError, MediaError)) else ERROR_TEXT
    await answer(interaction, text, ephemeral=True)

class Controls(discord.ui.View):
    def __init__(self, manager, player):
        super().__init__(timeout=None)
        self.manager, self.player = manager, player
        buttons = [('Play' if not player.current else ('Resume' if player.paused else 'Pause'), 'pause', discord.ButtonStyle.primary),
                   ('Skip', 'next', discord.ButtonStyle.secondary), ('Queue', 'queue', discord.ButtonStyle.secondary),
                   ('Stop', 'stop', discord.ButtonStyle.danger), ('Leave', 'leave', discord.ButtonStyle.secondary)]
        for label, action, style in buttons:
            b = discord.ui.Button(label=label, style=style)
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
                    elif action == 'queue': await answer(interaction, '', embed=queue_embed(p))
                except Exception as exc: await callback_error(interaction, exc)
            b.callback = on_click
            self.add_item(b)

class Results(discord.ui.View):
    def __init__(self, manager, channel_id, tracks):
        super().__init__(timeout=180)
        self.manager, self.channel_id, self.tracks = manager, channel_id, tracks
        self.claims = {}
        self.expires = time.monotonic() + 180
        self.claim_lock = asyncio.Lock()
        for index, track in enumerate(tracks):
            for action in ('Play', 'Add to queue'):
                button = discord.ui.Button(label=f'{action} #{index+1}', row=index,
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
                    except Exception as exc:
                        if claimed:
                            async with self.claim_lock: self.claims.pop(key, None)
                        await callback_error(interaction, exc)
                button.callback = on_click
                self.add_item(button)


def search_embed(tracks):
    embed = discord.Embed(title='Kristy • YouTube search', color=0xB79AFF,
                          description='Audio only. Play starts when idle; Add to queue only queues.')
    for i, t in enumerate(tracks):
        embed.add_field(name=f'{i+1}. {clean(t.title, 140)}', value=f'{duration(t.duration)} • [YouTube]({t.url})', inline=False)
    embed.set_footer(text='Results expire in 3 minutes • Controls work only for members in this VC')
    return embed


def queue_embed(player):
    embed = discord.Embed(title='Kristy • Queue', color=0xB79AFF)
    lines = []
    if player.current: lines.append('Now: ' + clean(player.current.title))
    for i, t in enumerate(list(player.queue)[:20]): lines.append(f'{i+1}. {clean(t.title, 95)} • {duration(t.duration)}')
    if len(player.queue) > 20: lines.append(f'...and {len(player.queue)-20} more')
    embed.description = '\n'.join(lines) or 'Queue is empty.'
    return embed


def player_panel(manager, player, status=None):
    t = player.current
    embed = discord.Embed(title='Kristy • Audio player', color=0xB79AFF)
    if t:
        embed.description = f'**{clean(t.title)}**\n{duration(t.duration)} • [YouTube]({t.url})'
        embed.add_field(name='Status', value=status or ('Paused' if player.paused else 'Playing'), inline=True)
        embed.add_field(name='Requested by', value=f'<@{t.requester}>', inline=True)
    else: embed.description = 'Ready. Search YouTube or use /play with a title or video link.'
    queued = list(player.queue)[:3]
    embed.add_field(name=f'Up next • {len(player.queue)}', value='\n'.join(clean(x.title, 90) for x in queued) or 'Queue is empty', inline=False)
    embed.set_footer(text='VC chat only • No AI for music controls • 5-minute idle leave')
    return embed, Controls(manager, player)
