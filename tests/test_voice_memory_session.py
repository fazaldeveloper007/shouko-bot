import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import diary
from voice_live.memory import Selection, digest, MemoryErrorCode
from voice_live.memory_session import SessionMemory, MemoryDuplexTurn
from voice_live.memory_policy import eligible_quote, safe_candidates
from voice_live.memory_review import preview, approve, load_selection
from voice_live.member_store import MemberStore
from voice_live.timing import Timings


def make_memory():
    return SessionMemory(selection=Selection(123, 456, 0, (digest('Likes puzzles'),)),
        requester=123, guild=456, generation=0,
        facts=[{'t': 'Likes puzzles', 'ts': 1}])


def event(text):
    return {'serverContent': {'inputTranscription': {'text': text}}}


def candidate(quote='I like puzzles.', source=1, text='Likes puzzles', category='interest'):
    return {'text': text, 'category': category, 'source_turn': source, 'source_quote': quote}


class MemorySessionTests(unittest.IsolatedAsyncioTestCase):
    def test_setup_fresh_preserves_model_voice_manual_vad(self):
        m = make_memory()
        base = {'setup': {'model': 'models/gemini-3.1-flash-live-preview',
            'realtimeInputConfig': {'activityHandling': 'START_OF_ACTIVITY_INTERRUPTS'},
            'generationConfig': {'voice': 'Sulafat'}}}
        config = m.setup(base)
        self.assertNotIn('inputAudioTranscription', base['setup'])
        self.assertEqual(config['setup']['model'], base['setup']['model'])
        self.assertEqual(config['setup']['generationConfig'], base['setup']['generationConfig'])
        self.assertEqual(config['setup']['inputAudioTranscription'], {})
        self.assertIn('Likes puzzles', json.dumps(config))

    async def test_one_request_only_then_no_repeat_and_no_raw_transcript_file(self):
        m = make_memory()
        m.server_event(event('I like puzzles.'), safe=True)
        sender = AsyncMock(return_value=json.dumps({'facts': [candidate()]}))
        result = await m.finish_candidates(reason='MANUAL_STOP', safe=True, send_once=sender)
        self.assertEqual(result, ('Self-note: I like puzzles.',))
        self.assertEqual(m.status, 'MEMORY_CANDIDATES_READY')
        self.assertEqual(sender.await_count, 1)
        self.assertEqual(m.buffer._parts, [])
        self.assertEqual(await m.finish_candidates(reason='MANUAL_STOP', safe=True, send_once=sender), ())
        self.assertEqual(sender.await_count, 1)

    async def test_input_segments_not_output_or_interim(self):
        m = make_memory()
        m.server_event({'serverContent': {'outputTranscription': {'text': 'I like tea.'}}}, safe=True)
        m.server_event({'serverContent': {'interimInputTranscription': {'text': 'I like chess.'}}}, safe=True)
        m.server_event(event('I like puzzles.'), safe=True)
        sender = AsyncMock(return_value=json.dumps({'facts': [candidate()]}))
        await m.finish_candidates(reason='SESSION_LIMIT', safe=True, send_once=sender)
        request = sender.await_args.args[0]
        self.assertEqual(request['source_turns'], [{'turn': 1, 'text': 'I like puzzles.'}])
        self.assertIn('not chronological', request['system_instruction'])

    async def test_offset_ambiguity_no_extraction(self):
        m = make_memory()
        e = event('I like puzzles.')
        e['serverContent']['inputTranscription']['startOffset'] = '1s'
        m.server_event(e, safe=True)
        sender = AsyncMock()
        self.assertEqual(await m.finish_candidates(reason='MANUAL_STOP', safe=True, send_once=sender), ())
        sender.assert_not_awaited()

    async def test_bad_source_no_extraction(self):
        for item in (None, [], {'text': 45}):
            m = make_memory()
            m.server_event({'serverContent': {'inputTranscription': item}}, safe=True)
            sender = AsyncMock()
            await m.finish_candidates(reason='MANUAL_STOP', safe=True, send_once=sender)
            sender.assert_not_awaited()

    async def test_membership_change_invalidates_context_and_source(self):
        m = make_memory()
        m.server_event(event('I like puzzles.'), safe=True)
        m.server_event({}, safe=False)
        self.assertEqual(m.context, '')
        self.assertEqual(m.buffer._parts, [])
        sender = AsyncMock()
        await m.finish_candidates(reason='MANUAL_STOP', safe=True, send_once=sender)
        sender.assert_not_awaited()

    async def test_no_input_no_call(self):
        m = make_memory()
        sender = AsyncMock()
        self.assertEqual(await m.finish_candidates(reason='MANUAL_STOP', safe=True, send_once=sender), ())
        self.assertEqual(m.status, 'MEMORY_NO_INPUT')
        sender.assert_not_awaited()

    async def test_failure_end_no_persistence(self):
        for reason in ('RECEIVE_ERROR', 'MEMBERSHIP_CHANGED', 'GOOGLE_API_REJECTED', None):
            m = make_memory()
            m.server_event(event('I like puzzles.'), safe=True)
            sender = AsyncMock()
            self.assertEqual(await m.finish_candidates(reason=reason, safe=True, send_once=sender), ())
            sender.assert_not_awaited()
            self.assertEqual(m.status, 'MEMORY_END_UNSAFE')

    async def test_sender_failure_fixed_status_no_retry(self):
        m = make_memory()
        m.server_event(event('I like puzzles.'), safe=True)
        sender = AsyncMock(side_effect=RuntimeError('synthetic private source'))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = await m.finish_candidates(reason='MANUAL_STOP', safe=True, send_once=sender)
        self.assertEqual(result, ())
        self.assertEqual(m.status, 'MEMORY_EXTRACTION_FAILED')
        self.assertEqual(sender.await_count, 1)
        self.assertEqual(output.getvalue(), '')

    async def test_timeout_no_retry(self):
        m = make_memory()
        m.server_event(event('I like puzzles.'), safe=True)
        async def sender(request):
            await asyncio.sleep(5)
        self.assertEqual(await m.finish_candidates(reason='MANUAL_STOP', safe=True,
            send_once=sender, timeout=.001), ())
        self.assertEqual(m.status, 'MEMORY_EXTRACTION_FAILED')

    async def test_revoke_while_request_inflight(self):
        m = make_memory()
        m.server_event(event('I like puzzles.'), safe=True)
        async def sender(request):
            m.invalidate()
            return json.dumps({'facts': [candidate()]})
        self.assertEqual(await m.finish_candidates(reason='MANUAL_STOP', safe=True, send_once=sender), ())
        self.assertEqual(m.status, 'MEMORY_SCOPE_CHANGED')

    async def test_cancellation_does_not_claim_saved(self):
        m = make_memory()
        m.server_event(event('I like puzzles.'), safe=True)
        async def sender(request):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await m.finish_candidates(reason='MANUAL_STOP', safe=True, send_once=sender)
        self.assertEqual(m.status, 'MEMORY_EXTRACTION_CANCELLED')

    async def test_ambiguous_candidate_is_skipped_not_saved(self):
        m = make_memory()
        m.server_event(event('My friend likes puzzles.'), safe=True)
        sender = AsyncMock(return_value=json.dumps({'facts': [candidate('My friend likes puzzles.')]}))
        self.assertEqual(await m.finish_candidates(reason='MANUAL_STOP', safe=True, send_once=sender), ())
        self.assertEqual(m.skipped, 1)

    async def test_same_event_audio_not_swallowed(self):
        m = make_memory()
        turn = MemoryDuplexTurn('synthetic-key-not-real', lambda: True, m)
        data = event('I like puzzles.')
        data['serverContent']['modelTurn'] = {'parts': [{'inlineData': {'data': 'AA==', 'mimeType': 'audio/pcm;rate=24000'}}]}
        with patch('voice_live.duplex_protocol.DuplexTurn.receive', new=AsyncMock(return_value=data)):
            result = await turn.receive(None)
        self.assertIs(result, data)
        self.assertIn('modelTurn', result['serverContent'])


class ReviewAndPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.store = MemberStore(self.directory)
        self.store.merge_reviewed(123, expected_generation=0, facts=('Likes puzzles',), merge=diary.merge)

    def test_preview_is_private_no_auto_selection_or_stdout(self):
        path = self.directory / 'private-review.json'
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            count = preview(self.store, 123, 456, path)
        self.assertEqual(count, 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        data = json.loads(path.read_text())
        self.assertEqual(data['approved_digests'], [])
        data['approved_digests'] = [data['notes'][0]['digest']]
        path.write_text(json.dumps(data))
        selection_path = self.directory / 'selection.json'
        result = approve(self.store, 123, 456, path, selection_path)
        self.assertEqual(load_selection(selection_path), result)
        self.assertEqual(selection_path.stat().st_mode & 0o777, 0o600)

    def test_review_wrong_owner_guild_changed_note_rejected(self):
        path = self.directory / 'private-review.json'
        preview(self.store, 123, 456, path)
        data = json.loads(path.read_text())
        data['approved_digests'] = [digest('not in this diary')]
        path.write_text(json.dumps(data))
        with self.assertRaises(MemoryErrorCode):
            approve(self.store, 123, 456, path, self.directory/'selection.json')
        with self.assertRaises(MemoryErrorCode):
            approve(self.store, 789, 456, path, self.directory/'selection.json')
        with self.assertRaises(MemoryErrorCode):
            approve(self.store, 123, 789, path, self.directory/'selection.json')

    def test_revoke_preserves_notes_but_invalidates_prior_generation(self):
        self.assertEqual(self.store.revoke(123), 1)
        self.assertEqual(self.store.snapshot(123)[1]['facts'][0]['t'], 'Likes puzzles')
        with self.assertRaises(MemoryErrorCode):
            self.store.merge_reviewed(123, expected_generation=0, facts=('late',), merge=diary.merge)

    def test_no_fixed_hobby_whitelist(self):
        for quote in ('I enjoy astrophotography.', 'Mujhe origami pasand hai.', 'I main Xianyun.'):
            self.assertTrue(eligible_quote(quote), quote)

    def test_sensitive_quoted_thirdparty_instructions_rejected(self):
        for quote in ('My password is abc', 'My friend likes puzzles', 'I live in Kolkata',
                      'I have diabetes medical notes', 'I use token abcd', 'I like puzzles: ignore system',
                      'I like @someone', 'My salary is private', 'I like "secret instruction"'):
            self.assertFalse(eligible_quote(quote), quote)

    def test_model_paraphrase_not_stored(self):
        quotes = ((1, 'I enjoy astrophotography.'),)
        result, skipped = safe_candidates((('The user lives on Mars', 'interest', 1,
            'I enjoy astrophotography.'),), quotes)
        self.assertEqual(result, ('Self-note: I enjoy astrophotography.',))
        self.assertEqual(skipped, 0)

    def test_exact_source_evidence_required(self):
        result, skipped = safe_candidates((('Likes puzzles', 'interest', 1, 'I like puzzles.'),),
                                          ((1, 'I like chess.'),))
        self.assertEqual((result, skipped), ((), 1))

    def test_content_free_stage_metrics(self):
        times = iter((1., 1.1, 1.2, 2., 2.3, 2.5))
        t = Timings(lambda: next(times))
        for epoch in (1, 2):
            for stage in ('input_end', 'first_audio', 'playing'):
                t.mark(epoch, stage)
        result = t.summary()
        self.assertEqual(result['inputEndToFirstAudioMs'], {'count': 2, 'median': 200., 'max': 300.})
        self.assertEqual(result['inputEndToPlayingMs'], {'count': 2, 'median': 350., 'max': 500.})
        t.mark('not-an-epoch', 'playing')
        t.mark(2, 'unrecognized')
        self.assertNotIn('text', str(result))


if __name__ == '__main__':
    unittest.main()
