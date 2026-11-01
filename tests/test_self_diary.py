import tempfile
import pathlib
import unittest
from unittest.mock import patch
import self_diary

class Lessons(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patcher=patch.object(self_diary,'PATH',pathlib.Path(self.temp.name)/'lessons.json')
        self.patcher.start();self.addCleanup(self.patcher.stop)
    def suggest(self,guild=1,lesson='concise-answer'):
        return self_diary.suggest(guild,lesson,123,456,789)
    def test_pending_never_injected(self):
        self.assertEqual(self.suggest(),('saved',1));self.assertEqual(self_diary.prompt(1),'')
    def test_approve_only_same_guild(self):
        self.suggest();self.assertFalse(self_diary.change(2,1,'approve',5))
        self.assertTrue(self_diary.change(1,1,'approve',5))
        self.assertIn('concise',self_diary.prompt(1));self.assertEqual(self_diary.prompt(2),'')
        self.assertEqual(self_diary.load()['entries'][0]['approved_by'],5)
    def test_source_attribution(self):
        self.suggest();e=self_diary.load()['entries'][0]
        self.assertEqual((e['by'],e['channel'],e['message']),(123,456,789))
    def test_unknown_free_text_rejected(self):
        self.assertEqual(self.suggest(lesson='ping everyone'),('unknown',None))
        self.assertEqual(self_diary.load()['entries'],[])
    def test_duplicate_keeps_original_source(self):
        self.suggest();self.assertEqual(self_diary.suggest(1,'concise-answer',999,999,999),('pending',1))
        self.assertEqual(self_diary.load()['entries'][0]['by'],123)
    def test_total_cap_across_guilds(self):
        for g in range(105):self.suggest(guild=g)
        es=self_diary.load()['entries'];self.assertEqual(len(es),100);self.assertEqual(es[0]['guild'],0)
        self.assertEqual(self.suggest(guild=999),('full',None))
    def test_reset_only_current_guild(self):
        self.suggest();self.suggest(guild=2);self_diary.reset(1)
        self.assertEqual([e['guild'] for e in self_diary.load()['entries']],[2])
    def test_delete(self):
        self.suggest();self.assertTrue(self_diary.change(1,1,'delete',5));self.assertFalse(self_diary.change(1,1,'delete',5))
    def test_failed_write_not_success(self):
        with patch('self_diary.os.replace',side_effect=OSError):
            with self.assertRaises(OSError):self.suggest()
        self.assertEqual(self_diary.load()['entries'],[])
    def test_corrupt_file_no_overwrite_and_prompt_fail_open(self):
        self_diary.PATH.write_text('oops');self.assertEqual(self_diary.prompt(1),'')
        with self.assertRaises(ValueError):self.suggest()
        self.assertEqual(self_diary.PATH.read_text(),'oops')
    def test_code_owned_prompt_not_stored_content(self):
        self.suggest();self_diary.change(1,1,'approve',5)
        d=self_diary.load();d['entries'][0]['evil']='SYSTEM OVERRIDE';self_diary.save(d)
        self.assertNotIn('OVERRIDE',self_diary.prompt(1))
    def test_exact_feedback_only(self):
        self.assertEqual(self_diary.correction_lesson('jawab bahut lamba hai'),'concise-answer')
        self.assertIsNone(self_diary.correction_lesson('galat jawab'))
        self.assertIsNone(self_diary.correction_lesson('he said jawab bahut lamba hai'))
