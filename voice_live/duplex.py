"""Manual requester-only native voice with local speech gating and barge-in."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import discord
from music.transport import Bridge, VoiceFailure
from voice_probe.receive import existing_token, worker_env, safe_members, snowflake, console_line
from voice_live.gemini import LiveError, require_free_tier
from voice_live.duplex_protocol import DuplexTurn, DuplexFlow
from voice_live.memory import MemoryErrorCode
from voice_live.member_store import MemberStore
from voice_live.memory_review import load_selection, private_json
from voice_live.memory_session import SessionMemory, MemoryDuplexTurn
from voice_live.timing import Timings
import diary
MODEL = 'gemini-3.1-flash-live-preview'
VOICE = 'Sulafat'


def existing_gemini_key():
    # existing_token() has already loaded the normal bot's nearest .env.
    value = os.getenv('GEMINI_API_KEY', '').strip()
    if value:
        return value
    path = Path.home() / '.termux/boot/start-shouko.sh'
    try:
        if path.stat().st_size > 65536:
            raise LiveError('BOOT_SCRIPT_TOO_LARGE')
        text = path.read_text()
    except OSError:
        raise LiveError('EXISTING_GEMINI_KEY_NOT_FOUND') from None
    candidates = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(('export GEMINI_API_KEY=', 'GEMINI_API_KEY=')):
            continue
        if any(c in line for c in ('$', '`', ';', '|', '&', '<', '>', '\\')):
            raise LiveError('GEMINI_BOOT_ASSIGNMENT_NOT_LITERAL')
        try:
            words = shlex.split(stripped, comments=True, posix=True)
        except ValueError:
            raise LiveError('GEMINI_BOOT_ASSIGNMENT_NOT_LITERAL') from None
        if words and words[0] == 'export':
            words = words[1:]
        if len(words) != 1 or not words[0].startswith('GEMINI_API_KEY='):
            raise LiveError('GEMINI_BOOT_ASSIGNMENT_NOT_LITERAL')
        value = words[0].partition('=')[2]
        if not value or any(c.isspace() for c in value):
            raise LiveError('GEMINI_BOOT_ASSIGNMENT_NOT_LITERAL')
        candidates.append(value)
    if len(candidates) != 1:
        raise LiveError('EXISTING_GEMINI_KEY_MISSING_OR_AMBIGUOUS')
    return candidates[0]


class LiveBridge(Bridge):
    def __init__(self, client, channel, requester, abort):
        super().__init__(client, channel, ROOT, 1, self.failure)
        self.requester = requester
        self.abort = abort
        self.timings = Timings()
        self.audio = asyncio.Queue(maxsize=96)
        self.result = asyncio.get_running_loop().create_future()

    def safe(self):
        return not self.abort.is_set() and safe_members(self.channel, self.requester, self.client.user.id)

    async def failure(self):
        self.abort.set()
        if not self.result.done():
            self.result.set_result({'code': 'VOICE_FAILURE'})

    async def connect(self, *, timeout, reconnect, self_deaf=False, self_mute=False):
        self.state = 'joining'
        self.future('voice_ready')
        self.p = await asyncio.create_subprocess_exec('node', str(Path(__file__).with_name('worker_duplex.cjs')),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=worker_env())
        self.reader = asyncio.create_task(self.read())
        await self.write({'type': 'init', 'guild': str(self.channel.guild.id),
            'channel': str(self.channel.id), 'user': str(self.client.user.id), 'target': str(self.requester)})
        await self.wait('voice_ready', min(timeout, 12))
        self.state = 'ready'

    async def dispatch(self, m):
        payload = m.get('payload', {})
        data = payload.get('d', {})
        ok = False
        try:
            if (self.state != 'joining' or payload.get('op') != 4 or
                    data.get('guild_id') != str(self.channel.guild.id) or
                    data.get('channel_id') != str(self.channel.id) or not self.safe()):
                raise LiveError('UNSAFE_JOIN')
            await self.channel.guild.change_voice_state(channel=self.channel, self_deaf=False, self_mute=False)
            ok = True
        except Exception:
            pass
        await self.write({'type': 'ack', 'id': m.get('id'), 'ok': ok})

    async def read(self):
        try:
            while line := await self.p.stdout.readline():
                if len(line) > 65536:
                    raise LiveError('IPC_TOO_LARGE')
                event = json.loads(line)
                kind = event.get('type')
                if kind == 'gateway_send':
                    task = asyncio.create_task(self.dispatch(event))
                    self.sends.add(task)
                    task.add_done_callback(self.sends.discard)
                elif kind in ('voice_ready', 'listening'):
                    future = self.waiters.get(kind)
                    if future and not future.done():
                        future.set_result(event)
                elif kind in ('speech_start', 'audio', 'speech_end'):
                    if not self.safe():
                        raise LiveError('MEMBERSHIP_CHANGED')
                    self.audio.put_nowait(event)
                    if kind == 'speech_start':
                        print('LIVE_USER_SPEECH: requester speech detected; old playback cleared.', flush=True)
                    elif kind == 'speech_end':
                        print('LIVE_REPLY_PENDING: local speech pause detected.', flush=True)
                elif kind == 'playing':
                    self.timings.mark(event.get('epoch'), 'playing')
                    print('LIVE_PLAYING: speaking; requester can interrupt. STOP ends session.', flush=True)
                elif kind == 'reply_done':
                    print('LIVE_WAITING: waiting for requester speech, 60s idle limit excludes playback.', flush=True)
                elif kind == 'result':
                    if not self.result.done():
                        self.result.set_result(event)
                    future = self.waiters.get('voice_ready')
                    if future and not future.done():
                        future.set_exception(VoiceFailure('LIVE_WORKER_ENDED'))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.abort.set()
        finally:
            if not self.result.done():
                self.result.set_result({'code': 'WORKER_EXITED'})
            future = self.waiters.get('voice_ready')
            if future and not future.done():
                future.set_exception(VoiceFailure('WORKER_EXITED'))

    async def disconnect(self, *, force):
        self.abort.set()
        # Stop audio before potentially slow leave acknowledgement.
        if self.p is not None and self.p.returncode is None:
            try:
                await asyncio.wait_for(self.write({'type': 'stop'}), .5)
            except Exception:
                self.p.kill()
        while not self.audio.empty():
            self.audio.get_nowait()
        return await super().disconnect(force=force)


async def exchange(bridge, key, memory=None):
    turn = MemoryDuplexTurn(key, bridge.safe, memory) if memory else DuplexTurn(key, bridge.safe)
    try:
        await turn.open()
        bridge.future('listening')
        await bridge.write({'type': 'listen'})
        await bridge.wait('listening', 1)
        print('LIVE_LISTENING: local requester monitoring active, earphones recommended. Speak naturally.', flush=True)
        await DuplexFlow(turn, bridge, bridge.timings if memory else None).run()
    finally:
        await turn.close()


async def memory_extract_once(key, request):
    import aiohttp
    if request.get('model') != 'gemini-3.5-flash-lite':
        raise LiveError('MEMORY_EXTRACTOR_NOT_APPROVED')
    endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent'
    payload = {'systemInstruction': {'parts': [{'text': request['system_instruction']}]},
        'contents': [{'role': 'user', 'parts': [{'text': json.dumps(request['source_turns'], ensure_ascii=False)}]}],
        'generationConfig': {'maxOutputTokens': 600, 'temperature': .2, 'responseMimeType': 'application/json'}}
    async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=18)) as session:
        async with session.post(endpoint, headers={'x-goog-api-key': key}, json=payload, allow_redirects=False) as response:
            if response.status != 200:
                raise LiveError('MEMORY_EXTRACTION_FAILED')
            # Bound bytes before decoding provider output, not after response.json.
            data = await response.content.read(32769)
            if len(data) > 32768:
                raise LiveError('MEMORY_EXTRACTION_FAILED')
            result = json.loads(data)
            parts = result['candidates'][0]['content']['parts']
            text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
            if len(text) > 8192:
                raise LiveError('MEMORY_EXTRACTION_FAILED')
            return text


def public_result(result, outcome=None):
    summary = {k: result.get(k, 0) for k in ('code', 'packets', 'inputBytes', 'outputBytes', 'outputNonzeroSamples', 'outputFramesRead', 'turnsCompleted', 'droppedChunks')}
    if result.get('receiveStage') in {'TRANSPORT_DECRYPT', 'RTP_PARSE', 'DAVE_DECRYPT', 'STREAM_OTHER', 'CONTRACT_MISMATCH'}:
        summary['receiveStage'] = result['receiveStage']
    if result.get('errorClass') in {'ERROR', 'TYPE_ERROR', 'RANGE_ERROR', 'SYNTAX_ERROR', 'OTHER'}:
        summary['errorClass'] = result['errorClass']
    if outcome:
        summary['workerCode'] = summary['code']
        summary['code'] = outcome
    return summary


async def run(requester, guild_id=None, confirmed_free_tier=False, memory_path=None):
    require_free_tier(confirmed_free_tier)  # Before credentials, Discord or Google.
    selection = load_selection(memory_path) if memory_path else None
    if selection and (selection.owner != requester or guild_id != selection.guild):
        raise LiveError('MEMORY_SCOPE_CHANGED')
    if not sys.stdin.isatty():
        raise LiveError('TERMINAL_REQUIRED')
    token = existing_token()
    key = existing_gemini_key()
    # Credential loader resolved nearest .env; now honor its existing memory
    # directory setting too, without importing bot.py or reading other diaries.
    if os.getenv('MEMORY_DIR'):
        diary.MEMORY_DIR = Path(os.environ['MEMORY_DIR']).expanduser()
    # Read-only dependency check, no API/network import side effects.
    check = await asyncio.create_subprocess_exec('node', '-e',
        "require('./voice_probe/node_modules/@discordjs/voice');require('./voice_probe/node_modules/opusscript');require('./voice_live/audio.cjs')",
        cwd=ROOT, env=worker_env(), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        if await asyncio.wait_for(check.wait(), 8):
            raise LiveError('NODE_DEPENDENCIES_UNAVAILABLE')
    finally:
        if check.returncode is None:
            check.kill()
            await check.wait()
    intents = discord.Intents.none()
    intents.guilds = intents.voice_states = True
    client = discord.Client(intents=intents)
    bridge = None
    launched = cleaned = False
    abort = asyncio.Event()

    async def cleanup():
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        abort.set()
        if bridge is None:
            print('LIVE_JOIN_NOT_REQUESTED', flush=True)
        else:
            left = False
            try:
                left = await bridge.disconnect(force=True)
            except BaseException:
                if bridge.p is not None and bridge.p.returncode is None:
                    bridge.p.kill()
                    await bridge.p.wait()
            print('LEAVE_CONFIRMED' if left else 'LEAVE_UNCONFIRMED: manually disconnect before normal restart.', flush=True)
        await client.close()

    @client.event
    async def on_ready():
        nonlocal launched, bridge
        if launched:
            return
        launched = True
        stop = task = safety = None
        outcome = None
        memory = None
        store = MemberStore(diary.MEMORY_DIR) if selection else None
        generation = None
        channel = None
        memory_watch = None
        try:
            channels = [ch for g in client.guilds if guild_id is None or g.id == guild_id
                        for ch in g.voice_channels if requester in ch.voice_states]
            if len(channels) != 1:
                raise LiveError('VC_MISSING_OR_AMBIGUOUS')
            channel = channels[0]
            if not safe_members(channel, requester, client.user.id):
                raise LiveError('VC_NOT_EMPTY')
            if any(client.user.id in ch.voice_states for ch in channel.guild.voice_channels):
                raise LiveError('BOT_ALREADY_IN_VC')
            if selection:
                generation, snapshot = await asyncio.to_thread(store.snapshot, requester)
                memory = SessionMemory(selection=selection, requester=requester,
                    guild=channel.guild.id, generation=generation, facts=snapshot['facts'])
                snapshot = None
            print(f'Requester {requester}; guild {channel.guild.id}; channel {channel.id}.')
            print(f'Native model {MODEL}; voice {VOICE}. Existing key Free tier confirmed by owner; not a billing lookup.')
            print('Only your microphone goes to Google. Free API inputs/replies may improve products and be human-reviewed.')
            if memory:
                print('Kristy short persona + your privately reviewed selected diary notes go to Google.')
                print('Temporary input text is kept in bounded memory, no audio/full transcript files.')
                print('One extra Google Gemini3.5FlashLite request after safe session end selects useful own-user notes.')
                print('Conservative source checks skip ambiguous notes; remaining proposals require your private end-session SAVE confirmation.')
                print('No per-reply extraction, paid/Groq fallback, source transcript logs or promises of crash-proof saving.')
            else:
                print('Use a NON-SENSITIVE test phrase. No chat history/diaries/transcripts/recording.')
            print('Normal bot must be stopped. Keep the whole voice session non-sensitive.')
            print('Speech ends on natural pause (~0.8s), 60s hard input cap; 10s reply cap; 600s total from START.')
            print('60s user-idle limit excludes Kristy playback. No guarantee of uninterrupted duration.')
            print('Local microphone monitoring continues during replies for barge-in; earphones needed against echo.')
            print('Only detected utterance audio plus up to 200ms onset buffer goes to Google, no idle stream.')
            print('Local energy detection can mistake noise/quiet speech. Any hard input/output cap ends session.')
            print('No unselected history or session resumption. Connection/quota/error stops, no reconnect/fallback.')
            print('Empty restricted VC only. Any terminal input or Ctrl+C stops. Type START within 60s to agree and start.', flush=True)
            if await asyncio.wait_for(console_line(), 60) != 'START':
                return
            if not safe_members(channel, requester, client.user.id):
                raise LiveError('MEMBERSHIP_CHANGED')
            bridge = LiveBridge(client, channel, requester, abort)
            stop = asyncio.create_task(console_line())
            safety = asyncio.create_task(abort.wait())
            async def session():
                await channel.connect(cls=lambda c, ch: bridge, timeout=12, reconnect=False,
                                      self_deaf=False, self_mute=False)
                await exchange(bridge, key, memory)
                await asyncio.shield(bridge.result)
            if memory:
                async def watch_memory():
                    while True:
                        await asyncio.sleep(1)
                        try:
                            current = load_selection(memory_path)
                            current_generation = await asyncio.to_thread(store.generation, requester)
                            if current != selection or current_generation != generation:
                                raise MemoryErrorCode('MEMORY_SCOPE_CHANGED')
                        except Exception:
                            memory.invalidate()
                            abort.set()
                            return
                memory_watch = asyncio.create_task(watch_memory())
            task = asyncio.create_task(session())
            done, _ = await asyncio.wait([task, stop, safety, bridge.result], timeout=600, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task
            elif bridge.result in done:
                pass  # Worker completed or failed; result is projected after cleanup.
            else:
                outcome = 'SESSION_LIMIT' if not done else ('SAFETY_STOP' if safety in done else 'MANUAL_STOP')
                print('LIVE_STOP: ' + outcome, flush=True)
        except Exception as error:
            outcome = error_code(error)
            report_error(error)
        finally:
            abort.set()
            # Cancel Google flow first; NativeTurn.finally closes WSS. Stop local output
            # immediately too, before awaiting that network close or Discord leave.
            if bridge and bridge.p and bridge.p.returncode is None:
                try:
                    await asyncio.wait_for(bridge.write({'type': 'stop'}), .5)
                except Exception:
                    bridge.p.kill()
            for pending in (task, stop, safety, memory_watch):
                if pending and not pending.done():
                    pending.cancel()
            await asyncio.gather(*(p for p in (task, stop, safety, memory_watch) if p), return_exceptions=True)
            await cleanup()
            if bridge and bridge.result.done():
                result = public_result(bridge.result.result(), outcome)
                print('LIVE_RESULT ' + json.dumps(result), flush=True)
                if memory:
                    print('LIVE_TIMINGS ' + json.dumps(bridge.timings.summary()), flush=True)
            if memory:
                # Discord stream/output/socket are closed before extraction. Do
                # not reuse bridge.safe(): cleanup deliberately set abort.
                worker_code = bridge.result.result().get('code') if bridge and bridge.result.done() else None
                reason = outcome or worker_code
                safe_end = bool(channel and safe_members(channel, requester, client.user.id)
                    and reason in ('MANUAL_STOP', 'SESSION_LIMIT', 'IDLE_LIMIT'))
                try:
                    current_selection = load_selection(memory_path)
                    current_generation, _ = await asyncio.to_thread(store.snapshot, requester)
                    safe_end = safe_end and current_selection == selection and current_generation == generation
                    facts = await memory.finish_candidates(reason=('USER_IDLE' if reason == 'IDLE_LIMIT' else reason),
                        safe=safe_end, send_once=lambda request: memory_extract_once(key, request))
                    if facts:
                        print('PRIVATE_MEMORY_REVIEW: voice has ended. Review these proposed self-notes locally only.')
                        print('They are not saved yet. Do not share this private review as diagnostics.')
                        for number, fact in enumerate(facts, 1):
                            print(str(number) + '. ' + fact)
                        print('Type SAVE to save this whole selection, or anything else to discard. Timeout120s discards.', flush=True)
                        try:
                            confirmed = await asyncio.wait_for(console_line(), 120) == 'SAVE'
                        except asyncio.TimeoutError:
                            confirmed = False
                        if not confirmed:
                            facts = ()
                            memory.status = 'MEMORY_NOT_SAVED_REVIEW'
                    if facts:
                        if load_selection(memory_path) != selection:
                            raise MemoryErrorCode('MEMORY_SCOPE_CHANGED')
                        saved_snapshot = await asyncio.to_thread(store.merge_reviewed, requester,
                            expected_generation=generation, facts=facts, merge=diary.merge)
                        memory.status = 'MEMORY_SAVED'
                        # New voice notes passed the agreed source policy. Retain
                        # their voice eligibility, but never auto-select unrelated
                        # new text notes or changed legacy facts.
                        from voice_live.memory import digest
                        current_generation, _ = await asyncio.to_thread(store.snapshot, requester)
                        if current_generation != generation or load_selection(memory_path) != selection:
                            raise MemoryErrorCode('MEMORY_SCOPE_CHANGED')
                        available = {digest(item['t']) for item in saved_snapshot['facts']}
                        chosen = tuple(d for d in selection.digests if d in available)
                        chosen += tuple(digest(t) for t in facts
                            if digest(t) in available and digest(t) not in chosen)
                        await asyncio.to_thread(private_json, memory_path, {
                            'owner': requester, 'guild': channel.guild.id,
                            'generation': generation, 'digests': list(dict.fromkeys(chosen))})
                    print('LIVE_MEMORY ' + json.dumps({'code': memory.status,
                        'saved': len(facts) if memory.status == 'MEMORY_SAVED' else 0,
                        'skipped': memory.skipped}), flush=True)
                except Exception:
                    had_saved = memory.status == 'MEMORY_SAVED'
                    memory.invalidate()
                    print('LIVE_MEMORY ' + json.dumps({'code': 'MEMORY_SAVED_ACCESS_UPDATE_FAILED' if had_saved else 'MEMORY_SAVE_FAILED', 'saved': len(facts) if had_saved else 0}), flush=True)
            print('Native session ended. Check leave status before restarting your normal bot.', flush=True)

    @client.event
    async def on_voice_state_update(member, before, after):
        if bridge is not None and bridge.state not in ('leaving', 'closed') and not bridge.safe():
            abort.set()
            try:
                await asyncio.wait_for(bridge.write({'type': 'stop'}), .5)
            except Exception:
                pass

    try:
        await asyncio.wait_for(client.start(token, reconnect=False), 860 if selection else 680)
    finally:
        await cleanup()
        key = token = None


def error_code(error):
    # Every message from a third party stays private. No traces or raw API text.
    code = str(error) if type(error) is LiveError else {
        asyncio.TimeoutError: 'TIMEOUT', discord.LoginFailure: 'DISCORD_LOGIN_REJECTED',
        FileNotFoundError: 'DEPENDENCY_NOT_FOUND'}.get(type(error), 'STARTUP_OR_SESSION_FAILED')
    return code


def report_error(error):
    print('LIVE_ERROR code=' + error_code(error), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', type=snowflake, required=True)
    parser.add_argument('--guild-id', type=snowflake)
    parser.add_argument('--confirmed-free-tier', action='store_true', help='Owner confirms the CURRENT existing Gemini key uses Free tier; not an automatic lookup')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.user_id, args.guild_id, args.confirmed_free_tier))
    except KeyboardInterrupt:
        print('Stopped manually. Check bot left VC before restart.')
    except Exception as error:
        report_error(error)
        sys.exit(1)
