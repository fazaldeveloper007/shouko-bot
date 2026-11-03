"""One Python gateway; token-free Node voice helper, explicit confirmed leave."""
import asyncio
import json
import os
import pathlib
import secrets
import discord
from .diagnostics import engine_code, engine_diag

class VoiceFailure(Exception):
    pass

class Bridge(discord.VoiceProtocol):
    def __init__(self, client, channel, media_dir, max_bytes, on_failure):
        super().__init__(client, channel)
        self.media_dir = media_dir
        self.max_bytes = max_bytes
        self.on_failure = on_failure
        self.p = None
        self.reader = None
        self.sends = set()
        self.waiters = {}
        self.lock = asyncio.Lock()
        self.state = 'new'
        self.leave_seen = asyncio.Event()
        self.leave_confirmed = False
        self.session_id = None
        self.seen = set()
        self.close_lock = asyncio.Lock()
        self.failure_reported = False
        self.failure_code = None
        self.transitioning = False

    async def write(self, value):
        async with self.lock:
            if not self.p or self.p.returncode is not None:
                raise VoiceFailure('WORKER_CLOSED')
            self.p.stdin.write((json.dumps(value) + '\n').encode())
            await self.p.stdin.drain()

    def future(self, key):
        f = asyncio.get_running_loop().create_future()
        self.waiters[key] = f
        return f

    async def wait(self, key, timeout):
        f = self.waiters.get(key)
        if f is None:
            raise VoiceFailure('MISSING_WAITER')
        try:
            return await asyncio.wait_for(asyncio.shield(f), timeout)
        finally:
            self.waiters.pop(key, None)
            if not f.done(): f.cancel()

    async def connect(self, *, timeout, reconnect, self_deaf=True, self_mute=False):
        self.state = 'joining'
        self.future('voice_ready')
        allowed = ('PATH', 'HOME', 'TMPDIR', 'PREFIX', 'LD_LIBRARY_PATH', 'LD_PRELOAD')
        env = {k: os.environ[k] for k in allowed if k in os.environ}
        self.p = await asyncio.create_subprocess_exec('node', str(pathlib.Path(__file__).with_name('worker.cjs')),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=env)
        self.reader = asyncio.create_task(self.read())
        try:
            await self.write(dict(type='init', guild=str(self.channel.guild.id), channel=str(self.channel.id),
                user=str(self.client.user.id), mediaDir=str(self.media_dir), maxBytes=self.max_bytes))
            await self.wait('voice_ready', min(timeout, 20))
            self.state = 'ready'
        except BaseException:
            await self.disconnect(force=True)
            raise

    async def dispatch(self, m):
        p = m.get('payload', {})
        d = p.get('d', {})
        ok = False
        try:
            if self.state != 'joining' or p.get('op') != 4 or d.get('guild_id') != str(self.channel.guild.id) or d.get('channel_id') != str(self.channel.id):
                raise VoiceFailure('OUT_OF_SCOPE_GATEWAY_REQUEST')
            await self.channel.guild.change_voice_state(channel=self.channel, self_deaf=True, self_mute=False)
            ok = True
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        if self.p.returncode is None:
            try: await self.write(dict(type='ack', id=m.get('id'), ok=ok))
            except Exception: pass

    async def read(self):
        try:
            while line := await self.p.stdout.readline():
                if len(line) > 65536: raise VoiceFailure('IPC_TOO_LARGE')
                m = json.loads(line)
                kind = m.get('type')
                if kind == 'gateway_send':
                    if self.state != 'joining': continue
                    task = asyncio.create_task(self.dispatch(m)); self.sends.add(task); task.add_done_callback(self.sends.discard)
                elif kind in ('transition_wait', 'transition_ready'):
                    self.transitioning = kind == 'transition_wait'
                elif kind == 'failure':
                    await self.failed('WORKER_' + engine_code(m.get('code')))
                elif kind == 'play_failed':
                    f = self.waiters.get(('playing', m.get('id')))
                    if f and not f.done(): f.set_exception(TimeoutError())
                elif kind in ('voice_ready', 'playing', 'ended', 'track_stopped', 'control'):
                    key = kind if kind == 'voice_ready' else (kind, m.get('id'))
                    f = self.waiters.get(key)
                    if f and not f.done(): f.set_result(m)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.failed('VOICE_IPC_FAILED')
        finally:
            if self.state not in ('leaving', 'closed'):
                await self.failed('VOICE_WORKER_EXITED')

    async def failed(self, code):
        if not self.failure_code:
            self.failure_code = code; engine_diag(code)
        for f in list(self.waiters.values()):
            if not f.done(): f.set_exception(VoiceFailure(code))
        if not self.failure_reported and self.state == 'ready':
            self.failure_reported = True
            # Callback must not be awaited by reader: manager shutdown joins reader.
            task = asyncio.create_task(self.on_failure())
            task.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)

    async def on_voice_state_update(self, data):
        if str(data.get('guild_id')) != str(self.channel.guild.id) or str(data.get('user_id')) != str(self.client.user.id): return
        if data.get('channel_id') is None:
            self.leave_seen.set()
            if self.state not in ('leaving', 'closed'): await self.failed('VOICE_REMOVED')
            return
        if str(data.get('channel_id')) != str(self.channel.id):
            await self.failed('VOICE_MOVED'); return
        if self.state not in ('joining', 'ready'): return
        session = data.get('session_id')
        if self.session_id and session and session != self.session_id:
            await self.failed('VOICE_SESSION_CHANGED'); return
        if session: self.session_id = session
        await self.forward('state', data)

    async def on_voice_server_update(self, data):
        if str(data.get('guild_id')) == str(self.channel.guild.id) and self.state in ('joining', 'ready'):
            await self.forward('server', data)

    async def forward(self, kind, data):
        key = (kind, json.dumps(data, sort_keys=True))
        if key in self.seen: return
        self.seen.add(key)
        await self.write(dict(type='event', kind=kind, data=data))

    async def play(self, path):
        async def transition_done():
            while self.transitioning:
                await asyncio.sleep(.05)
        await asyncio.wait_for(transition_done(), 11)
        token = secrets.token_hex(8)
        self.future(('playing', token)); ended = self.future(('ended', token))
        try:
            await self.write(dict(type='play', id=token, path=str(path)))
            await self.wait(('playing', token), 8)
        except BaseException:
            self.waiters.pop(('ended', token), None)
            if not ended.done(): ended.cancel()
            elif not ended.cancelled(): ended.exception()
            raise
        return token, ended

    async def stop_track(self):
        token = secrets.token_hex(8)
        self.future(('track_stopped', token))
        await self.write(dict(type='stop_track', id=token))
        await self.wait(('track_stopped', token), 3)
        for key, f in list(self.waiters.items()):
            if isinstance(key, tuple) and key[0] in ('ended', 'playing'):
                if not f.done(): f.cancel()
                self.waiters.pop(key, None)

    async def pause(self, paused):
        token = secrets.token_hex(8)
        self.future(('control', token))
        await self.write(dict(type='pause' if paused else 'resume', id=token))
        return bool((await self.wait(('control', token), 3)).get('ok'))

    async def disconnect(self, *, force):
        async with self.close_lock:
            if self.state == 'closed': return self.leave_confirmed
            self.state = 'leaving'
            tasks = list(self.sends)
            for task in tasks: task.cancel()
            if tasks: await asyncio.gather(*tasks, return_exceptions=True)
            self.leave_seen.clear()
            try:
                await asyncio.wait_for(self.channel.guild.change_voice_state(channel=None, self_deaf=True), 5)
                await asyncio.wait_for(self.leave_seen.wait(), 5)
                self.leave_confirmed = True
            except (Exception, asyncio.CancelledError):
                self.leave_confirmed = False
            if self.p and self.p.returncode is None:
                try:
                    await self.write(dict(type='stop'))
                    await asyncio.wait_for(self.p.wait(), 2)
                except (Exception, asyncio.CancelledError):
                    if self.p.returncode is None: self.p.kill()
                    await self.p.wait()
            if self.reader and self.reader is not asyncio.current_task():
                await asyncio.gather(self.reader, return_exceptions=True)
            for f in self.waiters.values():
                if not f.done(): f.cancel()
                elif not f.cancelled(): f.exception()
            self.waiters.clear(); self.seen.clear()
            self.cleanup(); self.state = 'closed'
            return self.leave_confirmed
