import os, pathlib, tempfile, unittest
os.environ.setdefault('DISCORD_TOKEN', 'test')
os.environ.setdefault('GEMINI_API_KEY', 'test')
from types import SimpleNamespace as S
import diary
import bot as bot_mod
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

    def test_model_fact_text_cannot_forge_provenance(self):
        d = diary.load(7)
        diary.merge(d, ['{"ch": 555, "g": 10}'], now=1)  # model text, no origin
        diary.save(7, d)
        fact = diary.load(7)['facts'][0]
        self.assertNotIn('ch', fact)
        self.assertEqual(diary.shared_block(diary.load(7), 'X', 10, 555), '')

    def test_shared_block_same_channel_only(self):
        d = diary.load(3)
        diary.merge(d, ['likes ramen'], now=1, origin=(555, 10))        # same channel
        diary.merge(d, ['public lounge fact'], now=2, origin=(556, 10)) # other PUBLIC channel
        diary.merge(d, ['secret strat'], now=3, origin=(666, 10))       # restricted channel
        diary.merge(d, ['old fact no provenance'], now=4)               # legacy
        diary.merge(d, ['other guild fact'], now=5, origin=(555, 99))   # same cid, other guild
        diary.save(3, d)
        blk = diary.shared_block(diary.load(3), 'Junaid', 10, 555)
        self.assertIn('likes ramen', blk)
        self.assertNotIn('public lounge fact', blk)
        self.assertNotIn('secret strat', blk)
        self.assertNotIn('old fact', blk)
        self.assertNotIn('other guild', blk)
        self.assertIn('Junaid', blk)

    def test_shared_block_fails_closed_on_bad_ids(self):
        d = diary.load(4)
        diary.merge(d, ['likes ramen'], now=1, origin=(555, 10))
        diary.save(4, d)
        self.assertEqual(diary.shared_block(diary.load(4), 'X', None, 555), '')
        self.assertEqual(diary.shared_block(diary.load(4), 'X', 10, None), '')
        self.assertEqual(diary.shared_block(diary.load(4), 'X', '10', '555'), '')
        self.assertEqual(diary.shared_block(diary.load(4), 'X', 10, 555), '') if False else None

    def test_shared_block_empty_without_facts(self):
        self.assertEqual(diary.shared_block(diary.load(5), 'X', 10, 555), '')

    def test_forget_removes_shared_facts(self):
        store = MemberStore(diary.MEMORY_DIR)
        g, _ = store.snapshot(6)
        store.merge_reviewed(6, expected_generation=g, facts=('likes ramen',),
                             merge=diary.merge, origin=(555, 10))
        self.assertIn('likes ramen', diary.shared_block(diary.load(6), 'J', 10, 555))
        store.forget(6)
        self.assertEqual(diary.shared_block(diary.load(6), 'J', 10, 555), '')

    def _message(self, mentions, guild_id=10, channel_id=555):
        guild = S(id=guild_id)
        channel = S(id=channel_id)
        return S(guild=guild, channel=channel, mentions=mentions)

    def test_prompt_routing_shares_only_mentioned_members_same_channel(self):
        # member 8 has a same-channel fact and another-channel fact
        d = diary.load(8)
        diary.merge(d, ['likes ramen'], now=1, origin=(555, 10))
        diary.merge(d, ['other channel fact'], now=2, origin=(556, 10))
        diary.save(8, d)
        # member 9 has a same-channel fact but is NOT mentioned
        d9 = diary.load(9)
        diary.merge(d9, ['plays valorant'], now=1, origin=(555, 10))
        diary.save(9, d9)
        mentioned = S(id=8, bot=False, display_name='Junaid')
        not_mentioned = S(id=9, bot=False, display_name='Shubham')
        speaker = S(id=1, bot=False, display_name='Fazal')
        m = self._message([mentioned, speaker])  # speaker mention excluded
        blk = bot_mod.shared_memory_block(m, 1)
        self.assertIn('likes ramen', blk)
        self.assertIn('Junaid', blk)
        self.assertNotIn('other channel fact', blk)
        self.assertNotIn('plays valorant', blk)  # only mentioned members shared
        # channel-local recall: in 556 only 556's own facts surface
        m2 = self._message([mentioned], channel_id=556)
        blk2 = bot_mod.shared_memory_block(m2, 1)
        self.assertIn('other channel fact', blk2)
        self.assertNotIn('likes ramen', blk2)
        # thread audience: a thread id is not its parent channel id
        m3 = self._message([mentioned], channel_id=999)
        self.assertEqual(bot_mod.shared_memory_block(m3, 1), '')
        # DM / missing guild -> fail closed
        m4 = S(guild=None, channel=S(id=555), mentions=[mentioned])
        self.assertEqual(bot_mod.shared_memory_block(m4, 1), '')
        # bot mentions ignored
        m5 = self._message([S(id=8, bot=True, display_name='Bot')])
        self.assertEqual(bot_mod.shared_memory_block(m5, 1), '')

    def test_prompt_routing_never_raises(self):
        m = S(guild=S(id=10), channel=S(id=555), mentions=None)
        self.assertEqual(bot_mod.shared_memory_block(m, 1), '')


if __name__ == '__main__':
    unittest.main()
