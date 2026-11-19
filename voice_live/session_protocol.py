"""Fixed 3.1/Sulafat session. No reconnect, persistent history or fallback."""
import asyncio
from voice_live.gemini import NativeTurn, LiveError


class SessionTurn(NativeTurn):
    def __init__(self, key, safe):
        super().__init__(key, safe, model='gemini-3.1-flash-live-preview', voice='Sulafat')
        self.provider_complete = False
        self.generation_done = False

    def setup(self):
        config = super().setup()
        config['setup']['systemInstruction'] = {'parts': [{'text':
            'You are Kristy, an AI voice prototype. Have a friendly Hinglish conversation. '
            'Reply briefly to each utterance using only this session as context. '
            'Do not claim to be human. Do not request personal information. '
            'No tools or external actions.'}]}
        return config

    async def begin_next(self):
        self.check()
        if not self.provider_complete:
            raise LiveError('PROVIDER_TURN_NOT_COMPLETE')
        self.input_bytes = self.output_bytes = 0
        self.input_ended = self.provider_complete = self.generation_done = False
        await self.ws.send_json({'realtimeInput': {'activityStart': {}}})

    async def receive(self, timeout):
        payload = await super().receive(timeout)
        content = payload.get('serverContent', {})
        if not isinstance(content, dict):
            raise LiveError('GOOGLE_EVENT_INVALID')
        if content.get('interrupted'):
            raise LiveError('GOOGLE_INTERRUPTED')
        model_turn = content.get('modelTurn') or {}
        if not isinstance(model_turn, dict) or not isinstance(model_turn.get('parts', []), list):
            raise LiveError('GOOGLE_EVENT_INVALID')
        if self.generation_done:
            parts = model_turn.get('parts', [])
            if any(isinstance(p, dict) and p.get('inlineData') for p in parts):
                raise LiveError('GOOGLE_AUDIO_AFTER_GENERATION')
        if content.get('turnComplete'):
            self.provider_complete = True
        if content.get('generationComplete'):
            self.generation_done = True
        return payload

    async def output(self, send):
        await super().output(send)
        if self.output_bytes >= 480000:
            # A truncated response does not silently seed an unheard session turn.
            raise LiveError('OUTPUT_LIMIT')
        while not self.provider_complete:
            self.check()
            try:
                await self.receive(8)
            except asyncio.TimeoutError:
                raise LiveError('GOOGLE_TURN_COMPLETE_TIMEOUT') from None
