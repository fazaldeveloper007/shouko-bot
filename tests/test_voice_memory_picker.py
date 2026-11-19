import json
from pathlib import Path
import tempfile
import unittest

import diary
from voice_live.member_store import MemberStore
from voice_live.memory import MemoryErrorCode, digest
from voice_live.memory_review import pick, parse_numbers, display_text, load_selection


class PrivatePicker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = MemberStore(self.root)
        self.store.merge_reviewed(123, expected_generation=0,
            facts=('Likes puzzles', 'Enjoys origami', 'Mains Xianyun'), merge=diary.merge)
        self.path = self.root/'selection.json'

    def run_pick(self, answers, **kwargs):
        iterator = iter(answers)
        self.output = []
        return pick(self.store, 123, 456, self.path, read=lambda:next(iterator),
                    write=self.output.append, private_terminal=True, **kwargs)

    def test_select_numbered_notes_no_digest_editing_no_diary_mutation(self):
        before = (self.root/'123.json').read_bytes()
        selected = self.run_pick(('1, 3', 'CONFIRM'))
        self.assertEqual(selected.digests, (digest('Likes puzzles'),digest('Mains Xianyun')))
        self.assertEqual((self.root/'123.json').read_bytes(), before)
        self.assertEqual(load_selection(self.path), selected)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('Likes puzzles', self.path.read_text())
        self.assertTrue(any('1. Likes puzzles' in x for x in self.output))
        self.assertTrue(any('count=2' in x for x in self.output))

    def test_none_explicitly_confirmed_is_empty_selection(self):
        selected = self.run_pick(('NONE','CONFIRM'))
        self.assertEqual(selected.digests, ())

    def test_no_default_selection_no_file_on_cancel_or_missing_confirm(self):
        for answers in (('',), ('CANCEL',), ('1',''), ('1','yes'), ('1','confirm')):
            with self.subTest(answers=answers):
                self.assertIsNone(self.run_pick(answers))
                self.assertFalse(self.path.exists())

    def test_cancel_keeps_previous_selection(self):
        self.run_pick(('2','CONFIRM'))
        before = self.path.read_bytes()
        self.run_pick(('3','CANCEL'))
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_choices_do_not_write_or_select_all(self):
        for answer in ('all','0','4','-1','1 1','1,,2','1;2','1-3','x'*257):
            with self.subTest(answer=answer), self.assertRaises(MemoryErrorCode):
                self.run_pick((answer,'CONFIRM'))
            self.assertFalse(self.path.exists())

    def test_nonprivate_terminal_refuses_before_diary_read(self):
        class NoRead:
            def snapshot(self, owner):
                raise AssertionError('should not read real diary')
        with self.assertRaisesRegex(MemoryErrorCode,'^MEMORY_PRIVATE_TERMINAL_REQUIRED$'):
            pick(NoRead(),123,456,self.path,private_terminal=False)

    def test_forget_during_picker_invalidates(self):
        answers = iter(('1','CONFIRM'))
        n=0
        def read():
            nonlocal n
            n+=1
            if n==2:
                self.store.forget(123)
            return next(answers)
        with self.assertRaisesRegex(MemoryErrorCode,'^MEMORY_SCOPE_CHANGED$'):
            pick(self.store,123,456,self.path,read=read,write=lambda x:None,private_terminal=True)
        self.assertFalse(self.path.exists())

    def test_changed_selected_note_rejected(self):
        answers=iter(('1','CONFIRM'));n=0
        def read():
            nonlocal n
            n+=1
            if n==2:
                data=json.loads((self.root/'123.json').read_text())
                data['facts'][0]['t']='Different private note'
                (self.root/'123.json').write_text(json.dumps(data))
            return next(answers)
        with self.assertRaisesRegex(MemoryErrorCode,'^MEMORY_SELECTION_STALE$'):
            pick(self.store,123,456,self.path,read=read,write=lambda x:None,private_terminal=True)
        self.assertFalse(self.path.exists())

    def test_terminal_control_and_bidi_rendered_literally(self):
        self.assertEqual(display_text('x\x1b[31m\u202ey'), 'x\\u001b[31m\\u202ey')
        self.assertEqual(display_text('मुझे origami पसंद है'), 'मुझे origami पसंद है')

    def test_empty_existing_store_does_not_make_blank_approval(self):
        self.store.forget(123)
        self.assertIsNone(self.run_pick(()))
        self.assertFalse(self.path.exists())
        self.assertTrue(any('MEMORY_NO_EXISTING_NOTES' in x for x in self.output))

    def test_eof_does_not_write_selection(self):
        def eof():
            raise EOFError()
        with self.assertRaises(EOFError):
            pick(self.store,123,456,self.path,read=eof,write=lambda x:None,private_terminal=True)
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
