"""Finite native-audio client. No calls are made at import time. No fallbacks."""
import asyncio
import base64
import json
import aiohttp

MODEL = 'gemini-3.8-live'
COMPARISON_MODEL = 'gemini-3.1-flash-live-preview'
ALLOWED_MODELS = (MODEL, COMPARISON_MODEL)
VOICE = 'Kore'
ENDPOINT = ('wss://generativelanguage.googleapis.com/ws/'
            'google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent')
PROMPT = ('You are Kristy, an AI voice prototype. Reply to the single test utterance '
          'in one short friendly Hinglish sentence. Do not claim to be human. '
          'Do not request personal information. No tools or external actions.')
SETUP = {'setup': {'model': 'models/' + MODEL,
    'generationConfig': {'responseModalities': ['AUDIO'], 'maxOutputTokens': 256,
        'speechConfig': {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': VOICE}}}},
    'systemInstruction': {'parts': [{'text': PROMPT}]},
    'realtimeInputConfig': {'automaticActivityDetection': {'disabled': True}}}}


class LiveError(RuntimeError):
    """Fixed public reason code only."""


def require_model(model):
    if model not in ALLOWED_MODELS:
        raise LiveError('MODEL_NOT_APPROVED')
    return model


def setup_for(model, voice=VOICE):
    require_model(model)
    if voice not in (VOICE, 'Sulafat'):
        raise LiveError('VOICE_NOT_APPROVED')
    # Fresh per-turn config, never mutate the default or select a fallback.
    import copy
    setup = copy.deepcopy(SETUP)
    setup['setup']['model'] = 'models/' + model
    setup['setup']['generationConfig']['speechConfig']['voiceConfig']['prebuiltVoiceConfig']['voiceName'] = voice
    return setup


def require_free_tier(confirmed):
    # This is an explicit owner assertion, NOT a Google billing lookup.
    # Never infer billing status from API-key syntax or from a successful call.
    if confirmed is not True:
        raise LiveError('EXISTING_KEY_FREE_TIER_NOT_CONFIRMED')


def decode_audio(value):
    if not isinstance(value, str) or len(value) > 1400000:
        raise LiveError('GOOGLE_AUDIO_TOO_LARGE')
    try:
        data = bytearray(base64.b64decode(value, validate=True))
    except Exception:
        raise LiveError('GOOGLE_AUDIO_INVALID') from None
    if len(data) % 2:
        data[:] = b'\0' * len(data)
        raise LiveError('GOOGLE_PCM_ALIGNMENT')
    return data


class NativeTurn:
    def __init__(self, key, safe, model=MODEL, voice=VOICE):
        setup_for(model, voice)  # Validate before holding a key or opening a socket.
        self.voice = voice
        self.model = require_model(model)
        self.key = key
        self.safe = safe
        self.session = self.ws = None
        self.input_bytes = self.output_bytes = 0
        self.input_ended = False

    def check(self):
        if not self.safe():
            raise LiveError('MEMBERSHIP_CHANGED')

    def setup(self):
        return setup_for(self.model, self.voice)

    async def open(self):
        self.check()
        self.session = aiohttp.ClientSession(trust_env=False,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=5, sock_read=None))
        try:
            # Header auth, never key in URL. No automatic reconnect or retry.
            self.ws = await asyncio.wait_for(self.session.ws_connect(ENDPOINT,
                headers={'x-goog-api-key': self.key}, max_msg_size=1500000,
                autoclose=True, autoping=True), 6)
            self.check()
            await self.ws.send_json(self.setup())
            response = await self.receive(6)
            if not isinstance(response.get('setupComplete'), dict):
                raise LiveError('GOOGLE_SETUP_REJECTED')
            await self.ws.send_json({'realtimeInput': {'activityStart': {}}})
        except BaseException:
            await self.close()
            raise

    async def receive(self, timeout):
        message = await asyncio.wait_for(self.ws.receive(), timeout)
        if message.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
            raise LiveError('GOOGLE_CONNECTION_CLOSED')
        try:
            payload = json.loads(message.data)
        except Exception:
            raise LiveError('GOOGLE_EVENT_INVALID') from None
        if not isinstance(payload, dict):
            raise LiveError('GOOGLE_EVENT_INVALID')
        if 'error' in payload:
            raise LiveError('GOOGLE_API_REJECTED')
        if payload.get('goAway') or payload.get('toolCall'):
            raise LiveError('GOOGLE_UNEXPECTED_ACTION')
        return payload

    async def input_audio(self, encoded):
        self.check()
        if self.input_ended:
            raise LiveError('INPUT_ALREADY_ENDED')
        data = decode_audio(encoded)
        try:
            if len(data) > 4096 or self.input_bytes + len(data) > 256000:
                raise LiveError('INPUT_BYTE_LIMIT')
            self.input_bytes += len(data)
            await self.ws.send_json({'realtimeInput': {'audio': {
                'data': encoded, 'mimeType': 'audio/pcm;rate=16000'}}})
        finally:
            data[:] = b'\0' * len(data)

    async def end_input(self):
        self.check()
        if not self.input_ended:
            self.input_ended = True
            await self.ws.send_json({'realtimeInput': {'activityEnd': {}}})

    async def output(self, send):
        if not self.input_ended:
            raise LiveError('INPUT_NOT_ENDED')
        while True:
            self.check()
            try:
                payload = await self.receive(8)
            except asyncio.TimeoutError:
                raise LiveError('GOOGLE_RESPONSE_IDLE_TIMEOUT') from None
            content = payload.get('serverContent', {})
            if not isinstance(content, dict):
                raise LiveError('GOOGLE_EVENT_INVALID')
            if content.get('interrupted'):
                raise LiveError('GOOGLE_INTERRUPTED')
            turn = content.get('modelTurn') or {}
            if not isinstance(turn, dict) or not isinstance(turn.get('parts', []), list):
                raise LiveError('GOOGLE_EVENT_INVALID')
            for part in turn.get('parts', []):
                if not isinstance(part, dict):
                    raise LiveError('GOOGLE_EVENT_INVALID')
                inline = part.get('inlineData')
                if inline is None:
                    continue  # No text or transcripts are logged/retained/forwarded.
                if not isinstance(inline, dict) or inline.get('mimeType', '').replace(' ', '') != 'audio/pcm;rate=24000':
                    raise LiveError('GOOGLE_OUTPUT_FORMAT')
                data = decode_audio(inline.get('data'))
                try:
                    available = min(len(data), 480000 - self.output_bytes)
                    for offset in range(0, available, 12000):
                        self.check()
                        block = data[offset:min(offset+12000, available)]
                        try:
                            await send({'type': 'output', 'data': base64.b64encode(block).decode('ascii')})
                            self.output_bytes += len(block)
                        finally:
                            block[:] = b'\0' * len(block)
                    if self.output_bytes >= 480000:
                        await send({'type': 'output_end'})
                        return
                finally:
                    data[:] = b'\0' * len(data)
            # Single-turn non-background-reasoning model: generationComplete
            # finishes audio production; turnComplete may wait for playback.
            if content.get('generationComplete') or content.get('turnComplete'):
                await send({'type': 'output_end'})
                return

    async def close(self):
        try:
            if self.ws is not None:
                try:
                    await asyncio.wait_for(self.ws.close(), 1)
                except (Exception, asyncio.CancelledError):
                    pass
        finally:
            try:
                if self.session is not None:
                    await asyncio.wait_for(self.session.close(), 1)
            finally:
                self.ws = self.session = None
                self.key = None
