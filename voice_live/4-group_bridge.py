"""Draft group bridge, not wired into any startup path."""
import asyncio
import json
from pathlib import Path
import re
from music.transport import Bridge, VoiceFailure
from voice_probe.receive import worker_env
from voice_live.gemini import LiveError
from voice_live.timing import Timings
ROOT = Path(__file__).resolve().parents[1]

_END_CODE = re.compile(r'[A-Z0-9_]{2,64}').fullmatch


def end_line(event):
    """Content-free end line: only this code's own fixed UPPER_SNAKE codes pass."""
    parts = []
    for key in ('code', 'receiveStage', 'errorClass'):
        value = event.get(key)
        if isinstance(value, str) and _END_CODE(value):
            parts.append(value)
    return 'LIVE_ENDED: ' + ('/'.join(parts) if parts else 'UNKNOWN')

class GroupBridge(Bridge):
    def __init__(self, client, channel, coordinator, abort):
        super().__init__(client, channel, ROOT, 1, self.failure)
        self.coordinator = coordinator
        self.abort = abort
        self.timings = Timings()
        self.audio = asyncio.Queue(maxsize=480)
        self.dropped_audio = 0
        self.result = asyncio.get_running_loop().create_future()

    def safe(self):
        if self.abort.is_set():
            return False
        try:
            self.coordinator.playback()
            return not self.coordinator.stopped
        except Exception:
            self.abort.set()
            return False

    async def failure(self):
        self.abort.set()
        if not self.result.done():
            self.result.set_result({'code': 'VOICE_FAILURE'})

    async def connect(self, *, timeout, reconnect, self_deaf=False, self_mute=False):
        self.state = 'joining'
        self.future('voice_ready')
        self.p = await asyncio.create_subprocess_exec('node', str(Path(__file__).with_name('worker_group.cjs')),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=worker_env())
        self.reader = asyncio.create_task(self.read())
        await self.write({'type': 'init', 'guild': str(self.channel.guild.id),
            'channel': str(self.channel.id), 'user': str(self.client.user.id), 'targets': sorted(self.coordinator.policy._roster)})
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
                    try:
                        self.audio.put_nowait(event)
                    except asyncio.QueueFull:
                        # A backed-up reader must cost frames, never the session;
                        # control events evict the oldest frame to get through.
                        self.dropped_audio += 1
                        if kind != 'audio':
                            try:
                                self.audio.get_nowait()
                                self.audio.put_nowait(event)
                            except Exception:
                                pass
                    if kind == 'speech_start':
                        print('LIVE_USER_SPEECH: participant speech detected; old playback cleared.', flush=True)
                    elif kind == 'speech_end':
                        print('LIVE_REPLY_PENDING: local speech pause detected.', flush=True)
                elif kind == 'overlap_held':
                    print('LIVE_OVERLAP_HELD: simultaneous speech held back; one speaker at a time, pause to take the floor.', flush=True)
                elif kind == 'transition_wait':
                    print('LIVE_CONNECTION_REFRESH: voice encryption updating; audio paused briefly.', flush=True)
                elif kind == 'transition_ready':
                    print('LIVE_CONNECTION_REFRESH: encryption restored; session continues.', flush=True)
                elif kind == 'hold':
                    print('LIVE_PAUSED: agreement needed from a new member; capture and playback paused.', flush=True)
                elif kind == 'resumed':
                    print('LIVE_RESUMED: agreement saved; session continues.', flush=True)
                elif kind == 'targets':
                    pass
                elif kind == 'playing':
                    self.timings.mark(event.get('epoch'), 'playing')
                    print('LIVE_PLAYING: speaking; participant can interrupt. STOP ends session.', flush=True)
                elif kind == 'session_draining':
                    print('LIVE_FINISHING_REPLY: session deadline reached; current reply may finish. Speaking or STOP ends it.', flush=True)
                elif kind == 'reply_done':
                    print('LIVE_WAITING: waiting for participant speech, 60s idle limit excludes playback.', flush=True)
                elif kind == 'result':
                    print(end_line(event), flush=True)
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
