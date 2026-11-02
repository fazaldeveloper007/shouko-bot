"""Opt-in command registration: local tree setup only; sync is separately gated."""
import asyncio
import logging
import os
import discord
from discord import app_commands
from .player import Manager, MusicError
from .media import MediaError
from . import natural
from .ui import answer, callback_error, queue_embed, player_panel, Results, clean

log = logging.getLogger('kristy.music')

class Music:
    def __init__(self, client):
        self.client = client
        self.manager = Manager(client)
        self.manager.panel_builder = lambda p, status: player_panel(self.manager, p, status)
        self.tree = app_commands.CommandTree(client)
        self.synced = False
        self.busy = set()
        self.register()

    def register(self):
        @self.tree.command(name='joinvckristy', description='Join your VC; use its built-in text chat')
        @app_commands.guild_only()
        async def join(interaction: discord.Interaction):
            await self.execute(interaction, 'join')

        @self.tree.command(name='leavevckristy', description='Clear queue and leave this VC')
        @app_commands.guild_only()
        async def leave(interaction: discord.Interaction):
            await self.execute(interaction, 'leave')

        @self.tree.command(name='search', description='Search regular YouTube for audio-only playback')
        @app_commands.guild_only()
        @app_commands.describe(query='YouTube title, video, podcast or link')
        async def search(interaction: discord.Interaction, query: str):
            await self.execute(interaction, 'search', query)

        @self.tree.command(name='play', description='Play/add a title or YouTube video link; resume if blank')
        @app_commands.guild_only()
        async def play(interaction: discord.Interaction, query: str = ''):
            await self.execute(interaction, 'play', query)

        @self.tree.command(name='pause', description='Pause audio in this VC')
        @app_commands.guild_only()
        async def pause(interaction: discord.Interaction): await self.execute(interaction, 'pause')

        @self.tree.command(name='next', description='Skip the current item')
        @app_commands.guild_only()
        async def next_track(interaction: discord.Interaction): await self.execute(interaction, 'next')

        @self.tree.command(name='queue', description='Show this VC queue')
        @app_commands.guild_only()
        async def queue(interaction: discord.Interaction): await self.execute(interaction, 'queue')

        @self.tree.command(name='stop', description='Stop audio and clear queue; stay in VC')
        @app_commands.guild_only()
        async def stop(interaction: discord.Interaction): await self.execute(interaction, 'stop')

    async def execute(self, interaction, action, query=''):
        try:
            self.manager.caller_channel(interaction.user, interaction.channel)
            await interaction.response.defer()
            if action == 'search':
                tracks = await self.manager.media.search(query, interaction.user.id)
                # Search can take time: reject stale membership before any result publication.
                self.manager.caller_channel(interaction.user, interaction.channel)
                await answer(interaction, view=Results(self.manager, interaction.channel_id, tracks))
                await self._repost(interaction)
                return
            if action == 'play' and query:
                tracks = await self.manager.media.search(query, interaction.user.id)
                p = await self.manager.get(interaction.user, interaction.channel, join=True)
                self.manager.caller_channel(interaction.user, interaction.channel)
                await p.add(tracks[0])
                await answer(interaction, 'Added: ' + clean(tracks[0].title) + (' Preparing audio; playback starts after extraction/download.' if not p.started else ' Waiting behind the current track.'))
                await self._repost(interaction)
                return
            p = await self.manager.get(interaction.user, interaction.channel, join=action == 'join')
            if action == 'join': await self.manager.panel(p)
            elif action == 'leave': await self.manager.remove(p, 'Left VC. Queue cleared.')
            elif action == 'play':
                if not await p.start_queue(): await p.pause(False)
            elif action == 'pause': await p.pause(True)
            elif action == 'next': await p.interrupt()
            elif action == 'stop': await p.interrupt(clear=True)
            elif action == 'queue': await answer(interaction, embed=queue_embed(p))
            if action == 'queue': await self._repost(interaction)
            if action != 'queue': await answer(interaction, 'Done.', ephemeral=True)
        except Exception as exc:
            await callback_error(interaction, exc)

    async def natural(self, message, text):
        """'Kristy <title> bajao' in a VC's own text chat: same path and
        checks as /play (first search result; queue behind a playing track,
        start when idle). Returns True when the message was handled."""
        if (not text or getattr(message.channel, 'type', None) != discord.ChannelType.voice
                or getattr(message.author, 'bot', False) or getattr(message, 'webhook_id', None)
                or getattr(message, 'message_snapshots', None)):
            return False
        query = natural.parse(text)
        if query is None:
            return False
        key = (message.guild.id, message.author.id)
        if key in self.busy: return True
        self.busy.add(key)
        try:
            if not query:
                await message.reply('Kaunsa gaana? Naam bhi bolo.', mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                return True
            self.manager.caller_channel(message.author, message.channel)
            tracks = await self.manager.media.search(query, message.author.id)
            p = await self.manager.get(message.author, message.channel, join=True)
            self.manager.caller_channel(message.author, message.channel)
            waiting = bool(p.current is not None or p.queue)
            await p.add(tracks[0])
            await message.reply(('Queue mein: ' if waiting else 'Laga rahi hoon: ') + clean(tracks[0].title), mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            if not p.closed and p.channel.id == message.channel.id:
                await self.manager.panel(p)
        except Exception as exc:
            from .ui import ERROR_TEXT
            reply = str(exc) if isinstance(exc, (MusicError, MediaError)) else ERROR_TEXT
            try: await message.reply(reply, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            except Exception: pass
        finally:
            self.busy.discard(key)
        return True

    async def _repost(self, interaction):
        # After a public command reply, re-post the player card so it stays at the chat bottom.
        p = self.manager.players.get(getattr(getattr(interaction, 'guild', None), 'id', None))
        if p and not p.closed and p.channel.id == interaction.channel_id:
            await self.manager.panel(p)

    async def ready(self):
        # No slash registration unless operator explicitly opts into deployment.
        if not self.synced and os.getenv('MUSIC_SYNC_COMMANDS') == '1':
            scope = os.getenv('MUSIC_TEST_GUILD_ID', '').strip()
            if scope and not scope.isdigit(): raise RuntimeError('Invalid music server ID')
            from .commands import register_commands
            await register_commands(self.client,self.tree,int(scope) if scope else None)
            self.synced = True

    async def voice_changed(self, member, before, after):
        p = self.manager.players.get(member.guild.id)
        if not p: return
        if member.id == self.client.user.id:
            if p.bridge and p.bridge.state not in ('leaving', 'closed') and (not after.channel or after.channel.id != p.channel.id):
                await self.manager.remove(p, 'Voice session ended. Queue cleared.')

    async def gateway_disconnected(self):
        # Do not silently resume voice under a changed main-gateway session.
        await self.manager.close()

    def context(self, channel): return self.manager.context(channel)
    async def close(self): await self.manager.close()
