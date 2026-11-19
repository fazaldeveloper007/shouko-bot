import pathlib
import tempfile
import unittest
import diary
from voice_live.automatic_diary import authorize, validate, OWNER, GUILD
from voice_live.member_store import MemberStore
from voice_live.memory import MemoryErrorCode, digest

class AutomaticOwnDiary(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=MemberStore(self.tmp.name)
        self.store.merge_reviewed(OWNER,expected_generation=0,facts=('Likes puzzles','Enjoys origami'),merge=diary.merge)

    def test_reads_all_own_notes_ignores_old_picker_and_future_notes_eligible(self):
        other=OWNER+1
        self.store.merge_reviewed(other,expected_generation=0,facts=('Other user private sentinel',),merge=diary.merge)
        before=(self.store.directory/f'{OWNER}.json').read_bytes()
        selection=authorize(self.store,OWNER,GUILD)
        self.assertEqual(len(selection.digests),2)
        self.assertEqual(before,(self.store.directory/f'{OWNER}.json').read_bytes())
        self.store.merge_reviewed(OWNER,expected_generation=0,facts=('Likes chess',),merge=diary.merge)
        self.assertIn(digest('Likes chess'),authorize(self.store,OWNER,GUILD).digests)
        scope=next(self.store.directory.glob('voice-automatic-*.json'))
        self.assertEqual(scope.stat().st_mode&0o777,0o600)
        self.assertNotIn('puzzles',scope.read_text())

    def test_wrong_actor_guild_and_missing_diary_no_blank_creation(self):
        for owner,guild in ((OWNER+1,GUILD),(OWNER,GUILD+1)):
            with self.assertRaisesRegex(MemoryErrorCode,'MEMORY_AUTOMATIC_SCOPE_NOT_APPROVED'):authorize(self.store,owner,guild)
        (self.store.directory/f'{OWNER}.json').unlink()
        with self.assertRaisesRegex(MemoryErrorCode,'MEMORY_EXISTING_DIARY_NOT_FOUND'):authorize(self.store,OWNER,GUILD)
        self.assertFalse((self.store.directory/f'{OWNER}.json').exists())

    def test_revoke_forget_and_preexisting_revoke_never_regrant(self):
        authorize(self.store,OWNER,GUILD)
        self.store.revoke(OWNER)
        with self.assertRaisesRegex(MemoryErrorCode,'MEMORY_AUTOMATIC_ACCESS_REVOKED'):validate(self.store,OWNER,GUILD,0)
        next(self.store.directory.glob('voice-automatic-*.json')).unlink()
        with self.assertRaisesRegex(MemoryErrorCode,'MEMORY_AUTOMATIC_ACCESS_REVOKED'):authorize(self.store,OWNER,GUILD)
