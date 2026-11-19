"""Manually run a 20-second, requester-only, local Discord receive/decode probe.
No bot.py import, AI calls, recordings, transcripts, playback or persistent audio.
Stop the normal bot before running. This starts its own temporary gateway.
"""
import argparse
import asyncio
import json
import os
import pathlib
import shlex
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import discord
from dotenv import load_dotenv
from music.transport import Bridge, VoiceFailure


class ProbeError(RuntimeError):
    """Fixed public reason code; never carries exception text or credentials."""
    pass


def report_error(error, stage):
    code = str(error) if isinstance(error, ProbeError) else {
        discord.LoginFailure: "DISCORD_LOGIN_REJECTED",
        discord.PrivilegedIntentsRequired: "DISCORD_INTENTS_REJECTED",
        FileNotFoundError: "NODE_NOT_FOUND",
        asyncio.TimeoutError: "TIMEOUT",
    }.get(type(error), "UNEXPECTED_" + type(error).__name__)
    print(f"PROBE_ERROR stage={stage} code={code}", flush=True)


def worker_env():
    allowed = ('PATH', 'HOME', 'TMPDIR', 'PREFIX', 'LD_LIBRARY_PATH', 'LD_PRELOAD')
    return {k: os.environ[k] for k in allowed if k in os.environ}


def snowflake(value):
    if not value.isascii() or not value.isdigit() or not 15 <= len(value) <= 22:
        raise argparse.ArgumentTypeError('Expected a Discord numeric user ID')
    return int(value)


def safe_members(channel, requester, bot_id):
    # Public discord.py channel API works even without member-cache completeness.
    ids = set(channel.voice_states)
    return requester in ids and not (ids - {requester, bot_id})


class ProbeBridge(Bridge):
    def __init__(self, client, channel, requester):
        super().__init__(client, channel, ROOT, 1, self.failure)
        self.requester = requester
        self.result = asyncio.get_running_loop().create_future()

    async def failure(self):
        if not self.result.done():
            self.result.set_result({'code': self.failure_code or 'VOICE_FAILURE'})

    async def connect(self, *, timeout, reconnect, self_deaf=False, self_mute=True):
        self.state = 'joining'
        self.future('voice_ready')
        env = worker_env()
        self.p = await asyncio.create_subprocess_exec(
            'node', str(pathlib.Path(__file__).with_name('worker.cjs')),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env)
        self.reader = asyncio.create_task(self.read())
        try:
            await self.write(dict(type='init', guild=str(self.channel.guild.id),
                                 channel=str(self.channel.id), user=str(self.client.user.id),
                                 target=str(self.requester)))
            await self.wait('voice_ready', min(timeout, 25))
            self.state = 'ready'
        except BaseException:
            await self.disconnect(force=True)
            raise

    async def dispatch(self, m):
        payload = m.get('payload', {})
        d = payload.get('d', {})
        ok = False
        try:
            if (self.state != 'joining' or payload.get('op') != 4
                    or d.get('guild_id') != str(self.channel.guild.id)
                    or d.get('channel_id') != str(self.channel.id)
                    or not safe_members(self.channel, self.requester, self.client.user.id)):
                raise VoiceFailure('UNSAFE_JOIN')
            await self.channel.guild.change_voice_state(channel=self.channel,
                                                        self_deaf=False, self_mute=True)
            ok = True
        except Exception:
            pass
        await self.write(dict(type='ack', id=m.get('id'), ok=ok))

    async def read(self):
        try:
            while line := await self.p.stdout.readline():
                if len(line) > 65536:
                    raise VoiceFailure('IPC_TOO_LARGE')
                m = json.loads(line)
                kind = m.get('type')
                if kind == 'gateway_send':
                    task = asyncio.create_task(self.dispatch(m))
                    self.sends.add(task)
                    task.add_done_callback(self.sends.discard)
                elif kind == 'voice_ready':
                    f = self.waiters.get('voice_ready')
                    if f and not f.done():
                        f.set_result(m)
                elif kind == 'result':
                    if not self.result.done():
                        self.result.set_result(m)
                    f = self.waiters.get('voice_ready')
                    if f and not f.done():
                        f.set_exception(VoiceFailure(m.get('code', 'PROBE_ENDED')))
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.failed('PROBE_IPC_FAILED')
        finally:
            if not self.result.done():
                self.result.set_result({'code': 'WORKER_EXITED'})
            f = self.waiters.get('voice_ready')
            if f and not f.done():
                f.set_exception(VoiceFailure('WORKER_EXITED'))


def public_result(result):
    summary = {k: result.get(k, 0) for k in
               ('code', 'packets', 'samples', 'nonzeroSamples', 'peak')}
    data = result.get('daveProof')
    if isinstance(data, dict):
        summary['daveProof'] = {key: data.get(key) for key in (
            'transportReady', 'protocol', 'sessionReady', 'privacyPresent',
            'reinitializing', 'transitionExecuted', 'pendingTransitions')}
    return summary


async def console_line():
    # No executor input thread that could keep Python alive after timeout.
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    def ready():
        if not future.done():
            future.set_result(sys.stdin.readline().strip())
    loop.add_reader(sys.stdin.fileno(), ready)
    try:
        return await future
    finally:
        loop.remove_reader(sys.stdin.fileno())


def normal_bot_dotenv():
    """Same nearest-ancestor .env lookup as bot.py's bare load_dotenv().

    Start at bot.py's directory, NOT this nested probe's directory or an
    unrelated current working directory. Stop at the first existing file,
    even when it lacks DISCORD_TOKEN, just as normal dotenv discovery does.
    """
    for directory in (ROOT, *ROOT.parents):
        candidate = directory / '.env'
        if candidate.is_file():
            return candidate
    return None


def existing_token():
    """Match documented launch env/.env, then literal Termux:Boot export only.

    Never source/execute the boot script, interpolate shell expressions,
    inspect other process environments, print or persist the credential.
    """
    dotenv_file = normal_bot_dotenv()
    if dotenv_file is not None:
        load_dotenv(dotenv_file)
    token = os.getenv('DISCORD_TOKEN', '').strip()
    if token:
        return token
    boot = pathlib.Path.home() / '.termux/boot/start-shouko.sh'
    try:
        if boot.stat().st_size > 65536:
            raise ProbeError('BOOT_SCRIPT_TOO_LARGE')
        text = boot.read_text()
    except FileNotFoundError:
        raise ProbeError('EXISTING_TOKEN_SOURCE_NOT_FOUND') from None
    except OSError:
        raise ProbeError('BOOT_SCRIPT_UNREADABLE') from None
    candidates = []
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith('export DISCORD_TOKEN=') or stripped.startswith('DISCORD_TOKEN=')):
            continue
        # Discord tokens are literal strings, not shell programs. Refuse dynamic
        # values, chaining or continuation; do not attempt to repair/reconstruct.
        if any(c in line for c in ('$', '`', ';', '|', '&', '<', '>', '\\')):
            raise ProbeError('BOOT_TOKEN_ASSIGNMENT_NOT_LITERAL')
        try:
            words = shlex.split(stripped, comments=True, posix=True)
        except ValueError:
            raise ProbeError('BOOT_TOKEN_ASSIGNMENT_NOT_LITERAL') from None
        if words and words[0] == 'export':
            words = words[1:]
        if len(words) != 1 or not words[0].startswith('DISCORD_TOKEN='):
            raise ProbeError('BOOT_TOKEN_ASSIGNMENT_NOT_LITERAL')
        value = words[0].partition('=')[2]
        if not value or any(c.isspace() for c in value):
            raise ProbeError('BOOT_TOKEN_ASSIGNMENT_NOT_LITERAL')
        candidates.append(value)
    if len(candidates) != 1:
        raise ProbeError('BOOT_TOKEN_ASSIGNMENT_MISSING_OR_AMBIGUOUS')
    return candidates[0]


async def run(requester, guild_id=None, check_only=False):
    if not sys.stdin.isatty():
        raise ProbeError('TERMINAL_REQUIRED')
    print("PROBE_STAGE preflight (no Discord connection yet)", flush=True)
    check = await asyncio.create_subprocess_exec(
        'node', '-e', "require('@discordjs/voice');require('@snazzah/davey');require('opusscript')",
        cwd=pathlib.Path(__file__).parent, env=worker_env(), stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        status = await asyncio.wait_for(check.wait(), 10)
    except BaseException:
        if check.returncode is None:
            check.kill()
        await check.wait()
        raise
    if status != 0:
        raise ProbeError('NODE_DEPENDENCIES_UNAVAILABLE')
    token = existing_token()
    if check_only:
        print('PREFLIGHT_OK: terminal, Node modules and existing token available. No Discord login or voice join attempted.', flush=True)
        return
    intents = discord.Intents.none()
    intents.guilds = True
    intents.voice_states = True
    client = discord.Client(intents=intents)
    launched = False
    bridge = None
    cleaned = False
    async def cleanup():
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        if bridge is None:
            print('PROBE_JOIN_NOT_REQUESTED: this run made no VC join request; existing bot VC state is not asserted.', flush=True)
        else:
            left = False
            try:
                left = await bridge.disconnect(force=True)
            except BaseException:
                # No success claim on cleanup failure. Kill any remaining helper.
                if bridge.p is not None and bridge.p.returncode is None:
                    bridge.p.kill()
                    await bridge.p.wait()
            print('LEAVE_CONFIRMED' if left else 'LEAVE_UNCONFIRMED: manually disconnect bot from VC before restart.', flush=True)
        await client.close()

    @client.event
    async def on_ready():
        nonlocal launched, bridge
        if launched:
            return  # no automatic recording/restart on reconnect
        launched = True
        stage = "VC_SELECTION"
        try:
            candidates = []
            for guild in client.guilds:
                if guild_id is not None and guild.id != guild_id:
                    continue
                for voice_channel in guild.voice_channels:
                    if requester in voice_channel.voice_states:
                        candidates.append(voice_channel)
            if len(candidates) != 1:
                print('STOP: requester must be in exactly one normal VC visible to this bot; use --guild-id if ambiguous.')
                return
            channel = candidates[0]
            if not safe_members(channel, requester, client.user.id):
                print('STOP: use an otherwise empty VC. No other people/bots may be present.')
                return
            if any(client.user.id in vc.voice_states for vc in channel.guild.voice_channels):
                print('STOP: bot is already in a VC. Stop the normal bot first.')
                return
            print(f'Requester {requester}; guild {channel.guild.id}; channel {channel.id}.')
            print('No audio is saved, transcribed or sent to AI. Metrics only. Normal bot must be stopped.')
            print('Type START within 60s to join and receive YOUR voice for at most 20s. Anything else cancels.')
            stage = 'AWAITING_MANUAL_START'
            if await asyncio.wait_for(console_line(), 60) != 'START':
                return
            if not safe_members(channel, requester, client.user.id):
                print('STOP: VC membership changed before start.')
                return
            stage = 'VC_JOIN_AFTER_START'
            print('PROBE_STAGE VC_JOIN_AFTER_START', flush=True)
            bridge = ProbeBridge(client, channel, requester)
            await channel.connect(cls=lambda c, ch: bridge, timeout=25, reconnect=False,
                                  self_deaf=False, self_mute=True)
            stage = 'RECEIVING'
            print('RECEIVE READY: speak now. Type STOP + Enter (or Ctrl+C) to end early; auto-stop in 20s.')
            stop = asyncio.create_task(console_line())
            try:
                done, _ = await asyncio.wait([stop, bridge.result], timeout=23,
                                             return_when=asyncio.FIRST_COMPLETED)
                if bridge.result not in done:
                    await bridge.write({'type': 'stop'})
                result = await asyncio.wait_for(asyncio.shield(bridge.result), 3)
                # Only allow a fixed diagnostic projection, never packets or text.
                print('RECEIVE_RESULT ' + json.dumps(public_result(result)))
                print('Decoded nonzero PCM supports receive/decode only; intelligibility and Live conversation remain UNTESTED.')
            finally:
                stop.cancel()
                await asyncio.gather(stop, return_exceptions=True)
        except asyncio.TimeoutError:
            report_error(ProbeError('TIMEOUT'), stage)
        except Exception as error:
            # Exception type only: third-party exception text can contain auth/transport data.
            report_error(error, stage)
            if bridge is not None and bridge.result.done():
                result = bridge.result.result()
                print('RECEIVE_RESULT ' + json.dumps(public_result(result)))
        finally:
            await cleanup()
            print('Test ended; decoder released, no audio files created. Restart your normal bot.')

    @client.event
    async def on_voice_state_update(member, before, after):
        if bridge is not None and bridge.state not in ('leaving', 'closed'):
            if not safe_members(bridge.channel, requester, client.user.id):
                try:
                    await bridge.write({'type': 'stop'})
                except Exception:
                    pass
    try:
        print('PROBE_STAGE DISCORD_LOGIN (voice join remains gated by START)', flush=True)
        await asyncio.wait_for(client.start(token, reconnect=False), 120)
    finally:
        await cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', required=True, type=snowflake)
    parser.add_argument('--guild-id', type=snowflake)
    parser.add_argument('--check-only', action='store_true', help='Local startup checks only; no Discord login or VC join')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.user_id, args.guild_id, args.check_only))
    except KeyboardInterrupt:
        print('Stopped manually; restart your normal bot after checking it left VC.')
    except Exception as error:
        report_error(error, 'STARTUP_OR_GATEWAY')
        print('No automatic retry. If no VC_JOIN_AFTER_START line appeared, this run did not request a VC join. Check existing VC state manually before restarting.')
        sys.exit(1)
