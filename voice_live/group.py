"""Manual restricted adult group test, no diary, no normal-bot activation.

Existing bot must be stopped before launch. Never run before notice approval,
current-key Free-tier confirmation, and restricted VC/audience checks.
"""
import argparse
import asyncio
import os
from pathlib import Path
import shlex
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import discord
from voice_probe.receive import existing_token, console_line
from voice_live.gemini import require_free_tier, LiveError
from voice_live.group_config import load_config, config_guilds, configured_scope
from voice_live.group_policy import GroupSessionPolicy, GroupPolicyError
from voice_live.group_runtime import (ConsentView, NOTICE_REVISION, DETAILS, draft_notice,
    discord_snapshot, run_approved_group)


def existing_key():
    # Read the same existing source, without importing diary-containing duplex.
    value = os.getenv('GEMINI_API_KEY', '').strip()
    if value:
        return value
    path = Path.home() / '.termux/boot/start-shouko.sh'
    if path.stat().st_size > 65536:
        raise LiveError('BOOT_SCRIPT_TOO_LARGE')
    candidates = []
    for line in path.read_text().splitlines():
        if not line.strip().startswith(('export GEMINI_API_KEY=', 'GEMINI_API_KEY=')):
            continue
        if any(c in line for c in ('$', '`', ';', '|', '&', '<', '>', '\\')):
            raise LiveError('GEMINI_BOOT_ASSIGNMENT_NOT_LITERAL')
        words = shlex.split(line, comments=True, posix=True)
        if words and words[0] == 'export':
            words = words[1:]
        if len(words) != 1 or not words[0].startswith('GEMINI_API_KEY='):
            raise LiveError('GEMINI_BOOT_ASSIGNMENT_NOT_LITERAL')
        candidates.append(words[0].partition('=')[2])
    if len(candidates) != 1 or not candidates[0] or any(c.isspace() for c in candidates[0]):
        raise LiveError('EXISTING_GEMINI_KEY_MISSING_OR_AMBIGUOUS')
    return candidates[0]


class SharedConsentView(discord.ui.View):
    def __init__(self, coordinator, agreed):
        super().__init__(timeout=720)
        self.coordinator, self.agreed = coordinator, agreed
        self.audience = tuple(coordinator.policy._roster)
        if set(self.audience) <= coordinator.policy._agreed:
            self.remove_item(self.agree_button)

    async def interaction_check(self, interaction):
        return (str(interaction.user.id) in self.audience and
            str(interaction.guild_id) == self.coordinator.policy.scope.guild)

    @discord.ui.button(label='18+ Agree, future sessions', style=discord.ButtonStyle.success)
    async def agree_button(self, interaction, button):
        voice = interaction.user.voice
        channel = voice.channel if voice else None
        try:
            self.coordinator.agree(actor=str(interaction.user.id),
                guild=str(interaction.guild_id), channel=str(channel.id) if channel else '',
                audience=self.audience, revision=NOTICE_REVISION, adult=True, processing=True, future=True)
            if self.coordinator.policy._agreed == set(self.audience):
                self.agreed.set()
            await interaction.response.send_message(
                'Agreement saved for future Live sessions with this allowed group. Revoke removes it. No microphone starts until everyone present has agreed.', ephemeral=True)
        except GroupPolicyError:
            await interaction.response.send_message('Agreement not accepted. Session or audience changed.', ephemeral=True)

    @discord.ui.button(label='Decline / Stop', style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction, button):
        self.coordinator.stop('GROUP_DECLINED')
        await interaction.response.send_message('Session stopped. No further voice processing.', ephemeral=True)


    @discord.ui.button(label='Details', style=discord.ButtonStyle.secondary)
    async def details_button(self, interaction, button):
        await interaction.response.send_message(DETAILS + '\nAllowed audience Discord IDs: ' +
            ', '.join(sorted(self.coordinator.policy.scope.allowlist)), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Revoke', style=discord.ButtonStyle.secondary)
    async def revoke_button(self, interaction, button):
        if self.coordinator.grant_store is not None:
            self.coordinator.grant_store.revoke(str(interaction.user.id))
        self.coordinator.stop('GROUP_CONSENT_REVOKED')
        await interaction.response.send_message('Your saved Live agreement is removed. Active session stopped.', ephemeral=True)

    async def on_timeout(self):
        self.coordinator.stop()


async def run(config_path, *, confirmed_free_tier=False, notice_approved=False):
    require_free_tier(confirmed_free_tier)
    if notice_approved is not True:
        raise GroupPolicyError('GROUP_NOTICE_APPROVAL_REQUIRED')
    raise GroupPolicyError('GROUP_USE_SLASH_WORKFLOW')
    data, ids = load_config(config_path)
    # Validate required actual channel before credentials or network.
    configured_scope(data, ids, '100000000000000000', sorted(config_guilds(data))[0])
    if not sys.stdin.isatty():
        raise GroupPolicyError('TERMINAL_REQUIRED')
    token = existing_token()
    key = existing_key()
    intents = discord.Intents.none()
    intents.guilds = intents.voice_states = True
    client = discord.Client(intents=intents)
    launched = False
    stopped = asyncio.Event()
    active_coordinator = None

    @client.event
    async def on_ready():
        nonlocal launched, active_coordinator
        if launched:
            return
        launched = True
        task = stop = None
        view = message = None
        try:
            scope = configured_scope(data, ids, client.user.id, sorted(config_guilds(data))[0])
            policy = GroupSessionPolicy(scope)
            channel = client.get_channel(int(scope.channel))
            if not isinstance(channel, discord.VoiceChannel):
                raise GroupPolicyError('GROUP_CHANNEL_UNAVAILABLE')
            roster = discord_snapshot(channel, scope)
            if data['owner_id'] not in roster['humans']:
                raise GroupPolicyError('GROUP_OWNER_NOT_PRESENT')
            if any(client.user.id in ch.voice_states for ch in channel.guild.voice_channels):
                raise GroupPolicyError('BOT_ALREADY_IN_VC')
            print('Restricted group non-sensitive test. Same3.1/Sulafat. No diary/history. Maximum8humans; overlap stops. Use earphones.', flush=True)
            print('Notice goes to this restricted VC text chat. Audio starts only after ALL present adults agree. Audience changes stop.', flush=True)
            print('Google Free data rules apply; no private/personal/confidential speech. No billing lookup or paid fallback.', flush=True)
            print('Type START within60s to post the approved notice. Any terminal input/Ctrl+C then stops.', flush=True)
            if await asyncio.wait_for(console_line(), 60) != 'START':
                return
            async def consent_ready(coordinator):
                nonlocal view, message, active_coordinator
                active_coordinator = coordinator
                agreed = asyncio.Event()
                view = SharedConsentView(coordinator, agreed)
                message = await channel.send(draft_notice(coordinator), view=view,
                    allowed_mentions=discord.AllowedMentions.none())
                await agreed.wait()
            task = asyncio.create_task(run_approved_group(client, channel, policy, key,
                consent_ready=consent_ready, stopped=stopped, notice_approved=True,
                confirmed_free_tier=True))
            stop = asyncio.create_task(console_line())
            done, _ = await asyncio.wait((task, stop), return_when=asyncio.FIRST_COMPLETED)
            if stop in done:
                stopped.set()
            await task
        except Exception as error:
            code = str(error) if type(error) in (LiveError, GroupPolicyError) else 'GROUP_SESSION_FAILED'
            print('GROUP_RESULT code=' + code, flush=True)
        finally:
            stopped.set()
            if view:
                view.stop()
            if message:
                try:
                    await message.edit(content='Restricted voice test ended. These controls no longer work.', view=None)
                except Exception:
                    print('GROUP_NOTICE_CLEANUP_UNCONFIRMED', flush=True)
            if stop and not stop.done():
                stop.cancel()
                await asyncio.gather(stop, return_exceptions=True)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await client.close()
            print('Group runner ended. Verify bot left VC before normal restart.', flush=True)

    @client.event
    async def on_voice_state_update(member, before, after):
        if active_coordinator:
            try:
                active_coordinator.refresh()
            except Exception:
                stopped.set()

    try:
        await asyncio.wait_for(client.start(token, reconnect=False), 900)
    finally:
        stopped.set()
        if active_coordinator:
            active_coordinator.stop()
        await client.close()
        token = key = None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-config', required=True)
    parser.add_argument('--confirmed-free-tier', action='store_true')
    parser.add_argument('--notice-approved', action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.private_config, confirmed_free_tier=args.confirmed_free_tier,
                        notice_approved=args.notice_approved))
    except KeyboardInterrupt:
        print('Stopped. Verify bot left VC before normal restart.')
    except Exception as error:
        code = str(error) if type(error) in (LiveError, GroupPolicyError) else 'GROUP_STARTUP_FAILED'
        print('GROUP_RESULT code=' + code)
        sys.exit(1)
