"""Offline-testable group adapter. No startup, registration, or provider calls.

Not wired into bot.py or existing runners. Notice text must be owner approved
before a future command activation. In-memory session only, never a diary.
"""
import asyncio
import discord
from voice_live.group_policy import GroupPolicyError
from voice_live.duplex_protocol import DuplexFlow, DuplexTurn
from voice_live.gemini import LiveError, require_free_tier

NOTICE_REVISION = 'group-free-future-v2'
NOTICE = (
    '18+ Kristy Live: mic goes to Google; free inputs/replies may improve its '
    'products and be human-reviewed. Everyone in this allowed group can hear '
    'replies. Agree saves permission for future sessions with this same group. '
    'No diary/history; no personal, confidential or sensitive talk. '
    'Details lists the group. Revoke anytime.'
)

DETAILS = (
    'Agreement permits processing of your microphone audio by Google Gemini '
    'Live (3.1 / Sulafat), for non-sensitive group voice conversations now and '
    'in future sessions in this same guild with this fixed allowed audience. '
    'Not everyone in the allowed audience has to be present. Their replies and '
    'your voice are audible in the VC. No diary, full local persona, chat history, '
    'memory extraction or voice recording/transcript files are used. Google Free '
    'data may improve products and be human-reviewed. No personal, confidential '
    'or sensitive information. Your Discord ID and this permission scope are '
    'saved locally, not sent to Google as an identity. Agree confirms you are '
    '18+ and allow this processing and future reuse. Revoke clears your grant; '
    'any active session stops. New allowed people, processing changes or notice '
    'changes need fresh agreement. Anyone new joining/leaving during an active '
    'session stops it. Decline / Stop stops this session; Revoke also removes '
    'future permission. Discord admin bypass and membership event latency mean '
    'we cannot guarantee an unexpected listener hears zero audio before stop.'
)



class GroupCoordinator:
    """Trusted adapter must refresh roster before every grant/audio/output."""
    def __init__(self, policy, snapshot, stop_worker, grant_store=None):
        self.grant_store = grant_store
        self.policy = policy
        self.snapshot = snapshot
        self.stop_worker = stop_worker
        self.stopped = False
        self.speaker = None
        self.epoch = 0

    def refresh(self):
        try:
            self.policy.observe_roster(**self.snapshot())
            if self.grant_store is not None:
                valid = self.grant_store.valid(self.policy.scope)
                if self.policy._active and not self.policy._roster <= valid:
                    raise GroupPolicyError('GROUP_CONSENT_REVOKED')
                self.policy._agreed = set(self.policy._roster & valid)
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.policy.stop()
        self.stop_worker()  # synchronous cancellation signal, not a network wait

    def agree(self, *, actor, guild, channel, audience, revision, adult, processing, future=False):
        self.refresh()
        self.policy.agree(actor=actor, guild=guild, channel=channel,
            audience=audience, notice_revision=revision, adult=adult, processing=processing)
        if self.grant_store is not None:
            try:
                self.grant_store.agree(self.policy.scope, actor, adult=adult,
                    processing=processing, future=future)
                self.refresh()
            except BaseException:
                self.stop()
                raise

    def start(self):
        self.refresh()
        self.policy.start()

    def input(self, event):
        try:
            self.refresh()
            actor = event.get('speaker')
            self.policy.require_audio(speaker=actor)
            kind, epoch = event.get('type'), event.get('epoch')
            if type(epoch) is not int:
                raise GroupPolicyError('GROUP_EPOCH_INVALID')
            if kind == 'speech_start':
                if self.speaker is not None or epoch != self.epoch + 1:
                    raise GroupPolicyError('GROUP_EPOCH_INVALID')
                self.speaker, self.epoch = actor, epoch
            elif kind in ('audio', 'speech_end'):
                if self.speaker != actor or self.epoch != epoch:
                    raise GroupPolicyError('GROUP_SPEAKER_MISMATCH')
                if kind == 'speech_end':
                    self.speaker = None
            else:
                raise GroupPolicyError('GROUP_INPUT_INVALID')
            return {k: v for k, v in event.items() if k != 'speaker'}
        except BaseException:
            self.stop()
            raise

    def playback(self):
        try:
            self.refresh()
            self.policy.require_playback()
        except BaseException:
            self.stop()
            raise


class GroupTurn(DuplexTurn):
    def setup(self):
        result = super().setup()
        result['setup']['systemInstruction'] = {'parts': [{'text':
            'You are Kristy, an AI in a restricted adult group voice test. '
            'Speak friendly Hinglish using only this session. Do not claim to '
            'recognize members or know their diaries, names or private history. '
            'Do not request or repeat personal, confidential or sensitive information. '
            'If someone starts sharing such information, ask them to stop and '
            'change to a non-sensitive topic. No tools or external actions.'}]}
        return result


class CheckedAudio:
    def __init__(self, queue, coordinator):
        self.queue, self.coordinator = queue, coordinator

    async def get(self):
        # Revalidate on dequeue, not just when IPC enqueues it.
        return self.coordinator.input(await self.queue.get())


class CheckedBridge:
    def __init__(self, bridge, coordinator):
        self.bridge, self.coordinator = bridge, coordinator
        self.audio = CheckedAudio(bridge.audio, coordinator)

    async def write(self, payload):
        if payload.get('type') in ('output', 'output_end'):
            self.coordinator.playback()
        await self.bridge.write(payload)


async def exchange_group(bridge, key, coordinator):
    coordinator.playback()
    def safe():
        try:
            coordinator.playback()
            return not coordinator.stopped
        except GroupPolicyError:
            return False
    turn = GroupTurn(key, safe)
    try:
        await turn.open()
        bridge.future('listening')
        await bridge.write({'type': 'listen'})
        await bridge.wait('listening', 1)
        await DuplexFlow(turn, CheckedBridge(bridge, coordinator)).run()
    finally:
        coordinator.stop()
        await turn.close()


class ConsentView(discord.ui.View):
    """Draft UI only. send is a future approved application command's job."""
    def __init__(self, coordinator, *, actor, audience):
        super().__init__(timeout=720)
        self.coordinator = coordinator
        self.actor = actor
        self.audience = tuple(audience)

    async def interaction_check(self, interaction):
        return (str(interaction.user.id) == self.actor and
                str(interaction.guild_id) == self.coordinator.policy.scope.guild)

    @discord.ui.button(label='I am 18+ and Agree', style=discord.ButtonStyle.success)
    async def agree_button(self, interaction, button):
        try:
            channel = interaction.user.voice.channel if interaction.user.voice else None
            self.coordinator.agree(actor=str(interaction.user.id),
                guild=str(interaction.guild_id), channel=str(channel.id) if channel else '',
                audience=self.audience, revision=NOTICE_REVISION, adult=True, processing=True, future=True)
            button.disabled = True
            await interaction.response.edit_message(content='Agreed for this session and audience. No microphone starts until everyone agrees. Decline / Stop remains available.', view=self)
        except GroupPolicyError:
            await interaction.response.edit_message(content='Agreement not accepted. Session or audience changed.', view=None)

    @discord.ui.button(label='Decline / Stop', style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction, button):
        self.coordinator.stop()
        await interaction.response.edit_message(content='Session stopped. No further voice processing.', view=None)
        self.stop()

    async def on_timeout(self):
        self.coordinator.stop()


def restricted_channel(channel, scope):
    """Conservative explicit per-member connect gate. Admins can bypass it."""
    if str(channel.id) != scope.channel or str(channel.guild.id) != scope.guild:
        raise GroupPolicyError('GROUP_CHANNEL_MISMATCH')
    base = channel.overwrites_for(channel.guild.default_role)
    if base.connect is not False or base.view_channel is not False:
        raise GroupPolicyError('GROUP_CHANNEL_NOT_RESTRICTED')
    allowed = scope.allowlist | {scope.bot}
    for target, overwrite in channel.overwrites.items():
        if overwrite.connect is True or overwrite.view_channel is True:
            if isinstance(target, discord.Role) or str(target.id) not in allowed:
                raise GroupPolicyError('GROUP_CONNECT_OVERWRITE_UNSAFE')


def discord_snapshot(channel, scope):
    restricted_channel(channel, scope)
    humans, bots = [], []
    for actor in channel.voice_states:
        if str(actor) == scope.bot:
            continue
        member = channel.guild.get_member(actor)
        if member is None:
            raise GroupPolicyError('GROUP_MEMBER_UNRESOLVED')
        (bots if member.bot else humans).append(str(actor))
    if len(humans) > 8:
        raise GroupPolicyError('GROUP_CONCURRENT_LIMIT')
    return dict(guild=str(channel.guild.id), channel=str(channel.id),
                humans=humans, other_bots=bots)


async def run_approved_group(client, channel, policy, key, *, consent_ready,
                             stopped, notice_approved=False, confirmed_free_tier=False, outcome=None, grant_store=None, validate_scope=None):
    """Draft lifecycle. Owner-reviewed adapter must collect actual member grants.

    consent_ready receives the coordinator, binds the agreed audience to each
    authenticated interaction, and returns only when all members agree. The
    caller owns Discord credentials/login and command activation. No fallback.
    No sends are performed by this lifecycle except voice gateway/playback.
    """
    from voice_live.group_bridge import GroupBridge
    if notice_approved is not True:
        raise GroupPolicyError('GROUP_NOTICE_APPROVAL_REQUIRED')
    require_free_tier(confirmed_free_tier)  # Assertion required, not billing proof.
    if policy.scope.notice_revision != NOTICE_REVISION:
        raise GroupPolicyError('GROUP_NOTICE_REVISION_MISMATCH')
    if str(client.user.id) != policy.scope.bot:
        raise GroupPolicyError('GROUP_BOT_ID_MISMATCH')
    abort = asyncio.Event()
    bridge = None
    def cancel():
        abort.set()
        stopped.set()
    def snapshot():
        if validate_scope is not None:
            validate_scope()
        return discord_snapshot(channel, policy.scope)
    coordinator = GroupCoordinator(policy, snapshot, cancel, grant_store)
    flow = watcher = consent = None
    async def watch():
        while not abort.is_set():
            coordinator.refresh()
            await asyncio.sleep(.025)
    async def session():
        if outcome is not None:
            outcome.leave_confirmed = False
        await channel.connect(cls=lambda c, ch: bridge, timeout=12,
            reconnect=False, self_deaf=False, self_mute=False)
        await exchange_group(bridge, key, coordinator)
    try:
        coordinator.refresh()
        watcher = asyncio.create_task(watch())
        consent = asyncio.create_task(consent_ready(coordinator))
        gate_stop = asyncio.create_task(stopped.wait())
        try:
            done, _ = await asyncio.wait((consent, watcher, gate_stop), timeout=120,
                return_when=asyncio.FIRST_COMPLETED)
            if consent not in done or stopped.is_set():
                if watcher in done:
                    await watcher
                raise GroupPolicyError('GROUP_CONSENT_NOT_COMPLETED')
            await consent
            coordinator.start()
            bridge = GroupBridge(client, channel, coordinator, abort)
            flow = asyncio.create_task(session())
            done, _ = await asyncio.wait((flow, watcher, gate_stop, bridge.result),
                timeout=600, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                await bridge.write({'type': 'session_deadline'})
                done, _ = await asyncio.wait((flow, watcher, gate_stop, bridge.result),
                    return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher
            if flow in done:
                await flow
        finally:
            gate_stop.cancel()
            await asyncio.gather(gate_stop, return_exceptions=True)
    finally:
        coordinator.stop()
        # Kill local playback first. Never wait for provider close before STOP.
        if bridge and bridge.p and bridge.p.returncode is None:
            try:
                await asyncio.wait_for(bridge.write({'type': 'stop'}), .5)
            except Exception:
                bridge.p.kill()
        for task in (flow, watcher, consent):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (flow, watcher, consent) if t), return_exceptions=True)
        confirmed = await bridge.disconnect(force=True) if bridge else True
        if outcome is not None:
            outcome.leave_confirmed = bool(confirmed)


def draft_notice(coordinator):
    """Only observed Discord IDs, no names/diaries or model-defined audience."""
    coordinator.refresh()
    return NOTICE + '\nCurrent audience Discord IDs: ' + ', '.join(sorted(coordinator.policy._roster))


async def show_approved_consent(interaction, coordinator, *, notice_approved=False):
    """Unregistered draft command handler. No call is made at import/startup."""
    if notice_approved is not True:
        raise GroupPolicyError('GROUP_NOTICE_APPROVAL_REQUIRED')
    coordinator.refresh()
    actor = str(interaction.user.id)
    channel = interaction.user.voice.channel if interaction.user.voice else None
    if (actor not in coordinator.policy._roster or
            str(interaction.guild_id) != coordinator.policy.scope.guild or
            not channel or str(channel.id) != coordinator.policy.scope.channel):
        raise GroupPolicyError('GROUP_ACTOR_NOT_PRESENT')
    view = ConsentView(coordinator, actor=actor,
        audience=coordinator.policy._roster)
    await interaction.response.send_message(draft_notice(coordinator), view=view,
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    return view
