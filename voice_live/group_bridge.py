"""Draft group bridge, not wired into any startup path."""
import asyncio
import json
from pathlib import Path
from music.transport import Bridge, VoiceFailure
from voice_probe.receive import worker_env
from voice_live.gemini import LiveError
from voice_live.timing import Timings
ROOT = Path(__file__).resolve().parents[1]

class GroupBridge(Bridge):
    def __init__(self, client, channel, coordinator, abort):
        super().__init__(client, channel, ROOT, 1, self.failure)
        self.coordinator = coordinator
        self.abort = abort
        self.timings = Timings()
        self.audio = asyncio.Queue(maxsize=96)
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
                    self.audio.put_nowait(event)
                    if kind == 'speech_start':
                        print('LIVE_USER_SPEECH: participant speech detected; old playback cleared.', flush=True)
                    elif kind == 'speech_end':
                        print('LIVE_REPLY_PENDING: local speech pause detected.', flush=True)
                elif kind == 'playing':
                    self.timings.mark(event.get('epoch'), 'playing')
                    print('LIVE_PLAYING: speaking; participant can interrupt. STOP ends session.', flush=True)
                elif kind == 'session_draining':
                    print('LIVE_FINISHING_REPLY: session deadline reached; current reply may finish. Speaking or STOP ends it.', flush=True)
                elif kind == 'reply_done':
                    print('LIVE_WAITING: waiting for participant speech, 60s idle limit excludes playback.', flush=True)
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
