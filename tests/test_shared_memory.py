import os, pathlib, tempfile, unittest
os.environ.setdefault('DISCORD_TOKEN', 'test')
os.environ.setdefault('GEMINI_API_KEY', 'test')
import diary
from voice_live.member_store import MemberStore


class SharedMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        diary.MEMORY_DIR = pathlib.Path(self.tmp.name)

    def test_merge_records_provenance_and_load_preserves_it(self):
        d = diary.load(1)
        diary.merge(d, ['likes ramen'], now=100, origin=(555, 10))
        diary.merge(d, ['plays valorant'], now=200)  # no provenance
        diary.save(1, d)
        facts = diary.load(1)['facts']
        self.assertEqual((facts[0]['t'], facts[0]['ch'], facts[0]['g']), ('likes ramen', 555, 10))
        self.assertNotIn('ch', facts[1])

    def test_merge_reviewed_threads_origin(self):
        store = MemberStore(diary.MEMORY_DIR)
        g, _ = store.snapshot(2)
        mem = store.merge_reviewed(2, expected_generation=g, facts=('main is Furina',),
                                   merge=diary.merge, origin=(555, 10))
        self.assertEqual((mem['facts'][0]['ch'], mem['facts'][0]['g']), (555, 10))
        g2, _ = store.snapshot(2)
        mem2 = store.merge_reviewed(2, expected_generation=g2, facts=('hates crowds',),
                                    merge=diary.merge)
        self.assertNotIn('ch', mem2['facts'][1])

    def _can_view(self, public, guild=10):
        def can_view(gid, cid):
            return gid == guild and cid in public
        return can_view

    def test_shared_block_includes_only_public_provenance_facts(self):
        d = diary.load(3)
        diary.merge(d, ['likes ramen'], now=1, origin=(555, 10))       # public
        diary.merge(d, ['secret strat'], now=2, origin=(666, 10))      # restricted
        diary.merge(d, ['old fact no provenance'], now=3)              # legacy
        diary.merge(d, ['other guild fact'], now=4, origin=(777, 99))  # other guild
        diary.save(3, d)
        blk = diary.shared_block(diary.load(3), 'Junaid', self._can_view({555}))
        self.assertIn('likes ramen', blk)
        self.assertNotIn('secret strat', blk)
        self.assertNotIn('old fact', blk)
        self.assertNotIn('other guild', blk)
        self.assertIn('Junaid', blk)

    def test_shared_block_fails_closed_when_channel_deleted_or_hidden(self):
        d = diary.load(4)
        diary.merge(d, ['likes ramen'], now=1, origin=(555, 10))
        diary.save(4, d)
        # channel deleted / no longer public -> nothing shared
        self.assertEqual(diary.shared_block(diary.load(4), 'Junaid', self._can_view(set())), '')
        # can_view raising -> fail closed
        def boom(gid, cid): raise RuntimeError('api down')
        self.assertEqual(diary.shared_block(diary.load(4), 'Junaid', boom), '')

    def test_shared_block_empty_without_facts(self):
        self.assertEqual(diary.shared_block(diary.load(5), 'X', self._can_view({1})), '')

    def test_forget_removes_shared_facts(self):
        store = MemberStore(diary.MEMORY_DIR)
        g, _ = store.snapshot(6)
        store.merge_reviewed(6, expected_generation=g, facts=('likes ramen',),
                             merge=diary.merge, origin=(555, 10))
        store.forget(6)
        self.assertEqual(diary.shared_block(diary.load(6), 'Junaid', self._can_view({555})), '')

    def test_public_source_gate(self):
        import bot as bot_mod
        from types import SimpleNamespace as S
        role = S()
        chan_ok = S(permissions_for=lambda r: S(view_channel=True))
        chan_no = S(permissions_for=lambda r: S(view_channel=False))
        guild = S(id=10, default_role=role, get_channel=lambda c: {555: chan_ok, 666: chan_no}.get(c))
        can_view = bot_mod._public_source(guild)
        self.assertTrue(can_view(10, 555))
        self.assertFalse(can_view(10, 666))   # restricted
        self.assertFalse(can_view(10, 999))   # deleted
        self.assertFalse(can_view(99, 555))   # other guild
        self.assertFalse(bot_mod._public_source(None)(10, 555))  # DM: fail closed


if __name__ == '__main__':
    unittest.main()
