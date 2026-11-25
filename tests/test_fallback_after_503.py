"""Gemini 503 x3 must not starve the Groq fallback (seen in the runtime log:
three Gemini 503s, then 'Groq exhausted calls=3/3' with no Groq attempt)."""
from unittest.mock import AsyncMock, patch

import retrying

try:
    from test_offline_harness import HarnessBase
    from test_retrying import Response, Session
except ImportError:
    from tests.test_offline_harness import HarnessBase
    from tests.test_retrying import Response, Session


class FallbackAfter503(HarnessBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.bot.GEMINI_API_KEY, self.bot.GROQ_API_KEY = 'g', 'q'

    def wire(self, script):
        session = Session(script)
        ok = {'choices': [{'message': {'content': 'groq reply'}, 'finish_reason': 'stop'}]}

        async def gem(channel_id, diary_block='', image_parts=None, extra_system=''):
            return await retrying.post_json(session, 'gemini', label='Gemini', headers={}, payload={})

        async def groq(channel_id, diary_block='', extra_system=''):
            data = await retrying.post_json(session, 'groq', label='Groq', headers={}, payload={},
                                            validate=retrying.generation_validator('Groq'))
            return data['choices'][0]['message']['content']
        for name, fn in (('think_gemini', gem), ('think_groq', groq)):
            p = patch.object(self.bot, name, fn)
            p.start()
            self.addCleanup(p.stop)
        return session, ok

    async def test_groq_runs_after_three_gemini_503s(self):
        ok = {'choices': [{'message': {'content': 'groq reply'}, 'finish_reason': 'stop'}]}
        session, _ = self.wire([Response(503), Response(503), Response(503), Response(200, ok)])
        with patch('retrying.asyncio.sleep', new=AsyncMock()):
            async with retrying.request_scope():
                out = await self.bot.think(CHAN := 555000)
        self.assertEqual(out, 'groq reply')
        self.assertEqual(session.calls, ['gemini'] * 3 + ['groq'])

    async def test_both_down_stays_bounded_at_six_calls(self):
        session, _ = self.wire([Response(503)] * 8)
        with patch('retrying.asyncio.sleep', new=AsyncMock()):
            async with retrying.request_scope():
                with self.assertRaises(retrying.RequestError):
                    await self.bot.think(555000)
        self.assertEqual(session.calls, ['gemini'] * 3 + ['groq'] * 3)

    async def test_blocked_gemini_still_does_not_fall_back(self):
        session, _ = self.wire([Response(200, {'promptFeedback': {'blockReason': 'SAFETY'}})])
        async def gem(*a, **k):
            raise retrying.RequestError('Gemini', 200, reason='blocked')
        with patch.object(self.bot, 'think_gemini', gem):
            with self.assertRaises(retrying.RequestError):
                await self.bot.think(555000)
        self.assertEqual(session.calls, [])
