import unittest
from gifs import GIFS

BAD = "https://media.tenor.com/FBojqU_OBfIAAAAM/anime-hello.gif"
NEW = "https://media.tenor.com/ufVk0BDFujwAAAAM/klee-klee-genshin-impact.gif"


class WelcomeGifPool(unittest.TestCase):
    def test_new_gif_added_once(self):
        self.assertEqual(GIFS["hello"].count(NEW), 1)

    def test_time_specific_good_morning_gif_not_in_hello_pool(self):
        # Its later frames show "GOOD MORNING!" which is wrong for welcomes.
        self.assertNotIn(BAD, GIFS["hello"])

    def test_pool_is_unique_and_keeps_existing_gifs(self):
        pool = GIFS["hello"]
        self.assertEqual(len(pool), len(set(pool)))
        self.assertIn("https://media.tenor.com/3g3D1mECft0AAAAM/anime-hi.gif", pool)
        self.assertIn("https://media.tenor.com/-ZDjEu483FkAAAAM/neuv-neuvillette.gif", pool)

    def test_good_morning_pool_untouched_by_welcome_pool(self):
        self.assertTrue(GIFS.get("good_morning"))
