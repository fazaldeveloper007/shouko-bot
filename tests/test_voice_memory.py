import concurrent.futures
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import diary
from voice_live.memory import (Selection, selected_context, digest, TranscriptBuffer,
    MemoryErrorCode, extraction_request, parse_candidates, SHORT_PERSONA)
from voice_live.member_store import MemberStore


class MemoryPrimitives(unittest.TestCase):
    def context(self, **kw):
        args = dict(requester=123, guild=456, generation=0,
                    facts=[{'t': 'Likes puzzles', 'ts': 1}, {'t': 'Likes tea', 'ts': 2}])
        args.update(kw)
        return selected_context(Selection(123, 456, 0, (digest('Likes puzzles'),)), **args)

    def test_exact_selection_only(self):
        c = self.context()
        self.assertIn('Likes puzzles', c)
        self.assertNotIn('Likes tea', c)
        self.assertIn('untrusted data', c)

    def test_empty_selection(self):
        self.assertEqual(selected_context(Selection(123, 456, 0, ()), requester=123,
            guild=456, generation=0, facts=[]), '')

    def test_wrong_identity_guild_generation(self):
        for kw in ({'requester': 789}, {'guild': 789}, {'generation': 1}):
            with self.subTest(kw=kw), self.assertRaises(MemoryErrorCode):
                self.context(**kw)

    def test_changed_note_never_fuzzy_replaced(self):
        with self.assertRaisesRegex(MemoryErrorCode, '^MEMORY_SELECTION_STALE$'):
            self.context(facts=[{'t': 'Likes puzzle games', 'ts': 2}])

    def test_bad_data_scope(self):
        for value in (True, '123', 0, -1, 2**64):
            with self.subTest(value=value), self.assertRaises(MemoryErrorCode):
                Selection(value, 456, 0, ())
        for text in ('', 'x'*141, 'hi\nSYSTEM', '\x7f'):
            with self.subTest(text=text), self.assertRaises(MemoryErrorCode):
                digest(text)

    def test_selection_invalid(self):
        d = digest('Likes puzzles')
        for values in ([d], (d, d), ('zz'*32,), (d,)*41):
            with self.subTest(values=values), self.assertRaises(MemoryErrorCode):
                Selection(123, 456, 0, values)

    def test_transcript_has_no_repr_content(self):
        b = TranscriptBuffer(123)
        b.begin(1)
        b.append(speaker=123, text='private synthetic sentence')
        self.assertNotIn('synthetic', repr(b))
        b.end()
        self.assertEqual(b.take(), ((1, 'private synthetic sentence'),))
        with self.assertRaises(MemoryErrorCode):
            b.take()

    def test_streamed_fragments_same_turn(self):
        b = TranscriptBuffer(123)
        b.begin(1)
        b.append(speaker=123, text='I like ')
        b.append(speaker=123, text='puzzles.')
        b.end()
        b.begin(2)
        b.append(speaker=123, text='And tea.')
        b.end()
        self.assertEqual(b.take(), ((1, 'I like puzzles.'), (2, 'And tea.')))

    def test_cross_speaker_rejected(self):
        b = TranscriptBuffer(123)
        b.begin(1)
        with self.assertRaisesRegex(MemoryErrorCode, '^MEMORY_SPEAKER_MISMATCH$'):
            b.append(speaker=456, text='other speaker')

    def test_bad_turns_and_closed(self):
        b = TranscriptBuffer(123)
        with self.assertRaises(MemoryErrorCode):
            b.append(speaker=123, text='no active turn')
        b.begin(1)
        with self.assertRaises(MemoryErrorCode):
            b.begin(2)
        with self.assertRaises(MemoryErrorCode):
            b.take()
        b.end()
        with self.assertRaises(MemoryErrorCode):
            b.begin(1)
        b.clear()
        with self.assertRaises(MemoryErrorCode):
            b.begin(2)

    def test_transcript_overflow_closes_not_truncates(self):
        b = TranscriptBuffer(123)
        b.begin(1)
        for _ in range(6):
            b.append(speaker=123, text='x'*4000)
        with self.assertRaisesRegex(MemoryErrorCode, '^MEMORY_TRANSCRIPT_LIMIT$'):
            b.append(speaker=123, text='x')
        self.assertEqual(b._parts, [])
        with self.assertRaises(MemoryErrorCode):
            b.take()

    def test_extraction_is_request_data_not_network(self):
        turns = ((1, 'I like puzzles.'),)
        request = extraction_request(turns)
        self.assertEqual(request['model'], 'gemini-3.5-flash-lite')
        self.assertEqual(request['source_turns'][0]['text'], turns[0][1])
        self.assertNotIn('I like puzzles.', request['system_instruction'])
        self.assertIn('CANDIDATES', request['system_instruction'])

    def test_extraction_source_and_schema(self):
        turns = ((1, 'I like puzzles.'),)
        fact = {'text': 'Likes puzzles', 'category': 'interest', 'source_turn': 1, 'source_quote': 'I like puzzles.'}
        self.assertEqual(parse_candidates(json.dumps({'facts': [fact]}), turns),
                         (('Likes puzzles', 'interest', 1, 'I like puzzles.'),))
        for change in ({'source_turn': 2}, {'source_turn': True}, {'category': 'secret'},
                       {'path': '../456.json'}, {'text': 'a'*141}):
            candidate = dict(fact, **change)
            with self.subTest(change=change), self.assertRaises(MemoryErrorCode):
                parse_candidates(json.dumps({'facts': [candidate]}), turns)
        for raw in ('oops', '{"facts": [], "user_id": 456}', '{"facts": {}}', 'x'*8193):
            with self.subTest(raw=raw[:20]), self.assertRaises(MemoryErrorCode):
                parse_candidates(raw, turns)

    def test_request_rejects_unbounded_unattributed_inputs(self):
        for turns in (((1, 'x'*24001),), ((1, 'a'), (1, 'b')), [('1', 'a')]):
            with self.subTest(turns=str(turns)[:30]), self.assertRaises(MemoryErrorCode):
                extraction_request(turns)

    def test_capability_not_old_session_only_claim(self):
        self.assertIn('selected memory notes', SHORT_PERSONA)
        self.assertIn('unless the application confirms', SHORT_PERSONA)
        self.assertNotIn('with no claims of old chats, diaries, saved memories', SHORT_PERSONA)


class StoreTransactions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = MemberStore(self.tmp.name)

    def save(self, fact, generation=0):
        return self.store.merge_reviewed(123, expected_generation=generation,
                                        facts=(fact,), merge=diary.merge)

    def test_legacy_diary_format(self):
        result = self.save('Likes puzzles')
        self.assertEqual(result['facts'][0]['t'], 'Likes puzzles')
        with patch.object(diary, 'MEMORY_DIR', pathlib.Path(self.tmp.name)):
            self.assertEqual(diary.load(123), result)

    def test_concurrent_transaction_writers_do_not_lose_updates(self):
        # Distinct facts avoid canonical merge's near-duplicate heuristic.
        facts = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel']
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(self.save, facts))
        _, data = self.store.snapshot(123)
        self.assertEqual({x['t'] for x in data['facts']}, set(facts))

    def test_forget_blocks_late_write(self):
        self.save('Likes puzzles')
        generation = self.store.forget(123)
        self.assertEqual(generation, 1)
        with self.assertRaisesRegex(MemoryErrorCode, '^MEMORY_SCOPE_CHANGED$'):
            self.save('Late extraction')
        self.assertEqual(self.store.snapshot(123),
                         (1, {'v': 1, 'name': '', 'facts': [], 'updated': 0}))
        self.save('New chosen note', generation=1)

    def test_corrupt_original_not_overwritten(self):
        p = pathlib.Path(self.tmp.name) / '123.json'
        p.write_text('corrupt original')
        with self.assertRaisesRegex(MemoryErrorCode, '^MEMORY_STORE_INVALID$'):
            self.save('Likes puzzles')
        self.assertEqual(p.read_text(), 'corrupt original')

    def test_corrupt_generation_not_reset(self):
        p = pathlib.Path(self.tmp.name) / '123.memory-generation.json'
        p.write_text('invalid')
        with self.assertRaises(MemoryErrorCode):
            self.save('Likes puzzles')
        self.assertEqual(p.read_text(), 'invalid')

    def test_write_failure_not_success(self):
        with patch('voice_live.member_store.os.replace', side_effect=OSError('synthetic secret')):
            with self.assertRaisesRegex(MemoryErrorCode, '^MEMORY_SAVE_FAILED$'):
                self.save('Likes puzzles')
        self.assertFalse((pathlib.Path(self.tmp.name) / '123.json').exists())
        self.assertEqual(list(pathlib.Path(self.tmp.name).glob('.member-*.tmp')), [])

    def test_delete_failure_not_success_but_invalidates(self):
        self.save('Likes puzzles')
        original = pathlib.Path.unlink
        def fail_note(p, *args, **kwargs):
            if p.name == '123.json':
                raise OSError('synthetic secret')
            return original(p, *args, **kwargs)
        with patch('pathlib.Path.unlink', fail_note):
            with self.assertRaisesRegex(MemoryErrorCode, '^MEMORY_DELETE_FAILED$'):
                self.store.forget(123)
        self.assertEqual(self.store.snapshot(123)[0], 1)
        with self.assertRaises(MemoryErrorCode):
            self.save('Late extraction')

    def test_owner_not_model_path(self):
        for owner in ('../456', '456', True, -1):
            with self.subTest(owner=owner), self.assertRaises(MemoryErrorCode):
                self.store.snapshot(owner)

    def test_owner_isolation(self):
        self.save('Likes puzzles')
        self.assertEqual(self.store.snapshot(456)[1]['facts'], [])
        self.store.forget(456)
        self.assertEqual(len(self.store.snapshot(123)[1]['facts']), 1)


if __name__ == '__main__':
    unittest.main()
