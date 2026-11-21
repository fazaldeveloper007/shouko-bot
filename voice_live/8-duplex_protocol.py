"""Single-reader manual-VAD protocol. No reconnect or server-side idle audio."""
import asyncio
import base64
from voice_live.gemini import NativeTurn, LiveError, decode_audio
from voice_live.session_protocol import SessionTurn


async def bounded(awaitable, seconds):
    # asyncio.wait_for in Python3.10 can lose a simultaneous outer cancellation.
    # Explicit task ownership keeps STOP from hanging between writes.
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait([task], timeout=seconds)
        if not done:
            raise asyncio.TimeoutError()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class DuplexTurn(SessionTurn):
    def __init__(self, key, safe):
        super().__init__(key, safe)

    def setup(self):
        config = super().setup()
        config['setup']['realtimeInputConfig']['activityHandling'] = 'START_OF_ACTIVITY_INTERRUPTS'
        # No application token cap or forced-short style for natural conversation.
        config['setup']['generationConfig'].pop('maxOutputTokens', None)
        config['setup']['systemInstruction'] = {'parts': [{'text':
            'You are Kristy, an AI voice prototype. Have a friendly Hinglish conversation. '
            'Give complete spoken answers using only this session as context. '
            'Do not claim to be human. Do not request personal information. '
            'No tools or external actions.'}]}
        return config

    async def receive(self, timeout):
        return await NativeTurn.receive(self, timeout)

    async def open(self):
        # NativeTurn.open() completes setup then calls this hook. No empty input turn.
        await super().open()

    async def start_initial_activity(self):
        pass


class DuplexFlow:
    def __init__(self, turn, bridge, timings=None):
        self.turn = turn
        self.timings = timings
        self.bridge = bridge
        self.epoch = 0
        self.active = False
        self.input_bytes = 0
        self.pending = None
        self.pending_bytes = 0
        self.pending_ended = False
        self.suppressed = False
        self.stale_turn_completes = 0
        self.interrupt_seen = False
        self.drain_interrupt_pending = False
        self.barrier = asyncio.Event()
        self.barrier.set()
        self.deadline = None

    async def send_input(self):
        while True:
            self.turn.check()
            event = await self.bridge.audio.get()
            kind, epoch = event.get('type'), event.get('epoch')
            if kind == 'speech_start':
                # Epochs are monotonic; a gap means events were dropped by a
                # pause/hold, which must not poison the next utterance.
                if self.active or type(epoch) is not int or epoch <= self.epoch:
                    raise LiveError('INPUT_EPOCH_INVALID')
                self.epoch = epoch
                self.active = True
                self.input_bytes = 0
                if self.pending is not None:
                    self.suppressed = True
                    self.interrupt_seen = False
                    self.deadline = None
                await bounded(self.turn.ws.send_json({'realtimeInput': {'activityStart': {}}}), 1)
            elif kind == 'audio':
                if not self.active or epoch != self.epoch:
                    raise LiveError('INPUT_EPOCH_INVALID')
                data = decode_audio(event.get('data'))
                try:
                    if len(data) > 640 or self.input_bytes + len(data) > 1920000:
                        raise LiveError('INPUT_BYTE_LIMIT')
                    self.input_bytes += len(data)
                    await bounded(self.turn.ws.send_json({'realtimeInput': {'audio': {
                        'data': event['data'], 'mimeType': 'audio/pcm;rate=16000'}}}), 1)
                finally:
                    data[:] = b'\0' * len(data)
            elif kind == 'speech_end':
                if not self.active or epoch != self.epoch or not self.input_bytes:
                    raise LiveError('INPUT_EPOCH_INVALID')
                # The interrupted turn closes with its own later turnComplete
                # (server order: interrupted > turn_complete). Gating activityEnd
                # on that close deadlocks: the server may hold the interrupted
                # turn's turnComplete until this activityEnd arrives. Credit the
                # stale close instead, since server chunks carry no client epoch.
                if self.suppressed:
                    self.stale_turn_completes += 1
                    self.drain_interrupt_pending = not self.interrupt_seen
                self.active = False
                self.pending = epoch
                self.pending_bytes = 0
                self.pending_ended = self.suppressed = False
                self.barrier.clear()
                self.deadline = asyncio.get_running_loop().time() + 15
                if self.timings is not None:
                    self.timings.mark(epoch, 'input_end')
                await bounded(self.turn.ws.send_json({'realtimeInput': {'activityEnd': {}}}), 1)
            else:
                raise LiveError('INPUT_EVENT_INVALID')

    async def receive_output(self):
        while True:
            self.turn.check()
            payload = await self.turn.receive(None)
            content = payload.get('serverContent', {})
            if not isinstance(content, dict):
                raise LiveError('GOOGLE_EVENT_INVALID')
            model = content.get('modelTurn') or {}
            if not isinstance(model, dict) or not isinstance(model.get('parts', []), list):
                raise LiveError('GOOGLE_EVENT_INVALID')
            parts = model.get('parts', [])
            if self.pending is None:
                if parts or any(content.get(k) for k in ('generationComplete','turnComplete','interrupted')):
                    if self.stale_turn_completes:
                        if content.get('turnComplete'):
                            self.stale_turn_completes -= 1
                            self.drain_interrupt_pending = False
                        continue
                    raise LiveError('GOOGLE_UNSOLICITED_OUTPUT')
                continue
            if not self.suppressed:
                self.deadline = asyncio.get_running_loop().time() + 10
            if content.get('interrupted'):
                if self.suppressed:
                    self.interrupt_seen = True
                    self.deadline = None
                    await self.bridge.write({'type': 'cancel_output', 'epoch': self.pending})
                elif self.drain_interrupt_pending:
                    # Playback was already cleared at speech_start.
                    self.drain_interrupt_pending = False
                else:
                    raise LiveError('GOOGLE_UNEXPECTED_INTERRUPTION')
            if not self.suppressed and not self.drain_interrupt_pending:
                if self.active:
                    raise LiveError('GOOGLE_OUTPUT_DURING_INPUT')
                for part in parts:
                    if not isinstance(part, dict):
                        raise LiveError('GOOGLE_EVENT_INVALID')
                    inline = part.get('inlineData')
                    if inline is None:
                        continue
                    if self.pending_ended:
                        raise LiveError('GOOGLE_AUDIO_AFTER_GENERATION')
                    if not isinstance(inline, dict) or inline.get('mimeType', '').replace(' ', '') != 'audio/pcm;rate=24000':
                        raise LiveError('GOOGLE_OUTPUT_FORMAT')
                    if self.timings is not None:
                        self.timings.mark(self.pending, 'first_audio')
                    data = decode_audio(inline.get('data'))
                    try:
                        for i in range(0, len(data), 12000):
                            block = data[i:i+12000]
                            try:
                                await self.bridge.write({'type': 'output', 'epoch': self.pending,
                                    'data': base64.b64encode(block).decode('ascii')})
                            finally:
                                block[:] = b'\0' * len(block)
                        self.pending_bytes += len(data)
                    finally:
                        data[:] = b'\0' * len(data)
                if (content.get('generationComplete') or content.get('turnComplete')) and not self.pending_ended:
                    self.pending_ended = True
                    await self.bridge.write({'type': 'output_end', 'epoch': self.pending})
                    if not self.suppressed:
                        self.deadline = asyncio.get_running_loop().time() + 15
            if content.get('turnComplete'):
                if self.stale_turn_completes:
                    self.stale_turn_completes -= 1
                    self.drain_interrupt_pending = False
                    continue
                self.pending = None
                self.pending_bytes = 0
                self.pending_ended = self.suppressed = False
                self.deadline = None
                self.barrier.set()

    async def recover_stale(self):
        """A stalled reply must cost one answer, never the whole session.

        The late turn, if it ever arrives, is dropped through the same stale
        credit used for interrupted turns. The socket itself is still healthy:
        a dead connection raises from receive() and remains terminal.
        """
        if self.pending is None:
            self.deadline = None
            return
        epoch = self.pending
        self.stale_turn_completes += 1
        self.pending = None
        self.pending_bytes = 0
        self.pending_ended = self.suppressed = False
        self.interrupt_seen = False
        self.drain_interrupt_pending = False
        self.deadline = None
        self.barrier.set()
        print('LIVE_REPLY_TIMEOUT: slow reply dropped; still listening.', flush=True)
        try:
            await bounded(self.bridge.write({'type': 'cancel_output', 'epoch': epoch}), 1)
        except Exception:
            pass

    async def watch(self):
        while True:
            await asyncio.sleep(.05)
            self.turn.check()
            if self.deadline is not None and asyncio.get_running_loop().time() >= self.deadline:
                await self.recover_stale()

    async def run(self):
        tasks = [asyncio.create_task(self.send_input()), asyncio.create_task(self.receive_output()), asyncio.create_task(self.watch())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
