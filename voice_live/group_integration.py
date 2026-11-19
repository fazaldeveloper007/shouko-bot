"""Opt-in /kristylive on the existing music tree, gateway and guild lock."""
import asyncio
import os
from types import SimpleNamespace
import discord
from discord import app_commands
from voice_live.group import SharedConsentView
from voice_live.group_config import load_config, configured_scope
from voice_live.group_policy import GroupSessionPolicy, GroupPolicyError
from voice_live.group_runtime import discord_snapshot, draft_notice, run_approved_group
from voice_live.gemini import require_free_tier
from music.player import MusicError
from music.ui import answer


class LiveGroups:
    def __init__(self, client, manager, config_path, *, notice_approved=False,
                 confirmed_free_tier=False):
        self.client, self.manager, self.config_path = client, manager, config_path
        self.notice_approved = notice_approved
        self.confirmed_free_tier = confirmed_free_tier
        self.sessions = {}

    def register(self, tree):
        @tree.command(name='kristylive', description='Start a restricted adult non-sensitive Live voice session')
        @app_commands.guild_only()
        async def kristylive(interaction: discord.Interaction):
            try:
                await self.start(interaction)
            except Exception as error:
                code = str(error) if type(error) is GroupPolicyError else 'GROUP_START_FAILED'
                await answer(interaction, code, ephemeral=True)

    async def start(self, interaction):
        require_free_tier(self.confirmed_free_tier)
        if self.notice_approved is not True:
            raise GroupPolicyError('GROUP_NOTICE_APPROVAL_REQUIRED')
        data, ids = load_config(self.config_path)
        vc = getattr(getattr(interaction.user, 'voice', None), 'channel', None)
        if not isinstance(vc, discord.VoiceChannel):
            raise GroupPolicyError('GROUP_JOIN_VOICE_FIRST')
        if str(vc.guild.id) != data['guild_id'] or str(interaction.guild_id) != data['guild_id']:
            raise GroupPolicyError('GROUP_GUILD_NOT_ALLOWED')
        if str(interaction.user.id) not in ids:
            raise GroupPolicyError('GROUP_CALLER_NOT_ALLOWED')
        # Exact channel comes from authenticated caller NOW, not screenshot/config default.
        scoped = dict(data, voice_channel_id=str(vc.id))
        scope = configured_scope(scoped, ids, self.client.user.id)
        discord_snapshot(vc, scope)
        me = vc.guild.me
        perms = vc.permissions_for(me)
        if not all(getattr(perms, p, False) for p in ('view_channel','connect','speak','send_messages')):
            raise GroupPolicyError('GROUP_BOT_PERMISSIONS_MISSING')
        gid = vc.guild.id
        lock = self.manager.guild_locks.setdefault(gid, asyncio.Lock())
        async with lock:
            if gid in self.manager.voice_reservations:
                raise GroupPolicyError('GROUP_VOICE_BUSY')
            p = self.manager.players.get(gid)
            if (p and not p.closed) or (me.voice and me.voice.channel) or vc.guild.voice_client:
                raise GroupPolicyError('GROUP_VOICE_BUSY')
            session = SimpleNamespace(channel=vc, stopped=asyncio.Event(), task=None,
                coordinator=None, view=None, message=None, leave_confirmed=True)
            self.sessions[gid] = session
            self.manager.voice_reservations[gid] = session
        try:
            await answer(interaction, 'Restricted Live consent started in your VC text chat. No microphone starts until everyone agrees. /leavevckristy stops it.', ephemeral=True)
            session.task = asyncio.create_task(self._run(session, GroupSessionPolicy(scope)))
        except BaseException:
            async with lock:
                self.sessions.pop(gid, None)
                self.manager.voice_reservations.pop(gid, None)
            raise

    async def _run(self, session, policy):
        gid = session.channel.guild.id
        async def consent(coordinator):
            session.coordinator = coordinator
            agreed = asyncio.Event()
            session.view = SharedConsentView(coordinator, agreed)
            session.message = await session.channel.send(draft_notice(coordinator),
                view=session.view, allowed_mentions=discord.AllowedMentions.none())
            await agreed.wait()
        try:
            key = os.getenv('GEMINI_API_KEY', '').strip()
            if not key:
                # Normal bot already loaded its environment. Never start another
                # client or inspect unrelated boot files inside slash execution.
                raise GroupPolicyError('GROUP_EXISTING_KEY_NOT_FOUND')
            await run_approved_group(self.client, session.channel, policy, key,
                consent_ready=consent, stopped=session.stopped, notice_approved=True,
                confirmed_free_tier=True, outcome=session)
        except asyncio.CancelledError:
            raise
        except Exception:
            # No model/user/exception text in logs or Discord diagnostics.
            pass
        finally:
            session.stopped.set()
            if session.view:
                session.view.stop()
            if session.message:
                try:
                    await session.message.edit(content='Restricted voice test ended. These controls no longer work.', view=None)
                except Exception:
                    pass
            lock = self.manager.guild_locks.setdefault(gid, asyncio.Lock())
            async with lock:
                if self.sessions.get(gid) is session:
                    self.sessions.pop(gid, None)
                # Do not let music steal an unconfirmed live voice connection.
                if session.leave_confirmed and self.manager.voice_reservations.get(gid) is session:
                    self.manager.voice_reservations.pop(gid, None)

    async def leave(self, interaction):
        gid = interaction.guild_id
        session = self.sessions.get(gid) or self.manager.voice_reservations.get(gid)
        if session is None:
            return False
        vc = getattr(getattr(interaction.user, 'voice', None), 'channel', None)
        if (not vc or vc.id != session.channel.id or
                str(interaction.user.id) not in self._allowed_ids()):
            raise MusicError('Join the active restricted VC to stop Live')
        await answer(interaction, 'Stopping Live and leaving VC.', ephemeral=True)
        session.stopped.set()
        if session.coordinator:
            session.coordinator.stop()
        if session.task:
            try:
                await asyncio.wait_for(asyncio.shield(session.task), 15)
            except asyncio.TimeoutError:
                session.task.cancel()
                await asyncio.gather(session.task, return_exceptions=True)
        if not session.leave_confirmed:
            await answer(interaction, 'Leave could not be confirmed. Disconnect Kristy manually before music or a new Live session.', ephemeral=True)
        return True

    def _allowed_ids(self):
        return load_config(self.config_path)[1]

    async def voice_changed(self, member, before, after):
        session = self.sessions.get(member.guild.id)
        if session and session.coordinator:
            try:
                session.coordinator.refresh()
            except Exception:
                session.stopped.set()
        if session and member.id == self.client.user.id and not after.channel:
            session.leave_confirmed = True

    async def close(self):
        sessions = list(self.sessions.values())
        for session in sessions:
            session.stopped.set()
            if session.coordinator:
                session.coordinator.stop()
        await asyncio.gather(*(s.task for s in sessions if s.task), return_exceptions=True)
