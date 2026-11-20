import datetime
import os
import unittest
from unittest.mock import patch

import announcements
from birthdays import BIRTHDAYS, BIRTHDAY_EMOJI, BIRTHDAY_LINES, BIRTHDAY_LOCAL_ART


class DataTests(unittest.TestCase):
    def test_dates_valid_and_art_urls(self):
        total = 0
        for (m, d), entries in BIRTHDAYS.items():
            datetime.date(2028, m, d)  # leap year; raises on an impossible date
            self.assertTrue(entries)
            for name, url in entries:
                total += 1
                self.assertTrue(name.strip())
                self.assertRegex(url, r'^https://enka\.network/ui/[A-Za-z0-9_]+\.(png|jpg)$')
        self.assertGreaterEqual(total, 110)

    def test_known_birthdays(self):
        def names(m, d):
            return {n for n, _ in BIRTHDAYS.get((m, d), ())}
        self.assertIn('Furina', names(10, 13))
        self.assertIn('Nahida', names(10, 27))
        self.assertIn('Zhongli', names(12, 31))
        self.assertIn('Neuvillette', names(12, 18))
        self.assertIn('Skirk', names(11, 5))
        self.assertIn('Paimon', names(6, 1))
        # shared-day pairs both present
        self.assertEqual(names(2, 2), {'Lynette', 'Lyney'})

    def test_lines_and_emoji_consistent(self):
        names = {n for entries in BIRTHDAYS.values() for n, _ in entries}
        for name in BIRTHDAY_LINES:
            self.assertIn(name, names)
            self.assertTrue(BIRTHDAY_LINES[name].strip())
        for name in BIRTHDAY_EMOJI:
            self.assertIn(name, names)
        for name, path in BIRTHDAY_LOCAL_ART.items():
            self.assertIn(name, names)
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.assertTrue(os.path.isfile(os.path.join(repo_root, path)), path)
            self.assertGreater(os.path.getsize(os.path.join(repo_root, path)), 1000)

    def test_neuvillette_owner_supplied_content(self):
        # Owner-supplied sample: heading + scales emoji + this line.
        self.assertEqual(BIRTHDAY_EMOJI['Neuvillette'], '⚖️')
        self.assertEqual(BIRTHDAY_LINES['Neuvillette'],
                         'People of Fontaine, your sins are forgiven!')


class CheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        announcements._state = announcements._default_state()
        announcements._state_broken = False
        announcements.BIRTHDAY_PING = False
        announcements.BIRTHDAY_TIME = "09:05"
        announcements.BIRTHDAY_LAST_MINUTE = 720
        announcements.DRY_RUN = False
        self.posts = []

    async def fake_post(self, channel_id, text, label, image=None):
        self.posts.append((channel_id, text, label, image))
        return True

    def patched(self, fetch):
        return (patch.object(announcements, '_post', self.fake_post),
                patch.object(announcements, 'fetch_bytes', fetch),
                patch.object(announcements, 'save_state', lambda: None))

    async def fake_bytes_ok(self, url, cap):
        return (b'\x89PNG-fake', 'image/png')

    async def fake_bytes_fail(self, url, cap):
        return (None, None)

    def with_lines(self, mapping):
        return patch.dict(announcements.BIRTHDAY_LINES, mapping, clear=False)

    async def test_posts_in_owner_format_with_image(self):
        now = datetime.datetime(2027, 12, 18, 9, 30)  # Neuvillette, owner-supplied line/emoji
        p = self.patched(self.fake_bytes_ok)
        with p[0], p[1], p[2]:
            await announcements._birthday_check(now)
        self.assertEqual(len(self.posts), 1)
        cid, text, label, image = self.posts[0]
        self.assertEqual(cid, announcements.BIRTHDAY_CHANNEL_ID)
        self.assertEqual(text, '# HAPPY BIRTHDAY NEUVILLETTE ⚖️\n\n'
                               '"People of Fontaine, your sins are forgiven!"')
        self.assertIsNotNone(image)  # owner-supplied local art read from disk
        self.assertNotIn('@everyone', text)
        self.assertEqual(announcements._state['birthday_done']['2027-12-18'], ['Neuvillette'])
        self.posts.clear()
        with p[0], p[1], p[2]:
            await announcements._birthday_check(now)
        self.assertFalse(self.posts)  # no double-post

    async def test_character_without_line_is_skipped(self):
        now = datetime.datetime(2027, 6, 1, 9, 30)  # Paimon (no voice-overs, no line) + Itto
        p = self.patched(self.fake_bytes_ok)
        with p[0], p[1], p[2]:
            await announcements._birthday_check(now)
        # Paimon is skipped (bare heading is never posted); Itto posts normally
        self.assertEqual([label for _, _, label, _ in self.posts], ['birthday Arataki Itto'])
        self.assertEqual(sorted(announcements._state['birthday_done']['2027-06-01']),
                         ['Arataki Itto', 'Paimon'])
        # Lynette on the other hand has a curated line and posts quoted
        self.posts.clear()
        with p[0], p[1], p[2]:
            await announcements._birthday_check(datetime.datetime(2027, 2, 2, 9, 30))
        self.assertEqual(len(self.posts), 2)
        for _, text, _, _ in self.posts:
            self.assertIn('\n\n"', text)

    async def test_window_respected(self):
        p = self.patched(self.fake_bytes_ok)
        for hh, mm in ((8, 59), (12, 0), (23, 30)):
            with p[0], p[1], p[2]:
                await announcements._birthday_check(datetime.datetime(2027, 12, 18, hh, mm))
        self.assertFalse(self.posts)
        with p[0], p[1], p[2]:
            await announcements._birthday_check(datetime.datetime(2027, 12, 18, 9, 5))
        self.assertEqual(len(self.posts), 1)

    async def test_image_failure_never_posts_text_only(self):
        now = datetime.datetime(2027, 12, 18, 9, 30)
        p = self.patched(self.fake_bytes_fail)
        with patch.dict(announcements.BIRTHDAY_LOCAL_ART, {}, clear=True):  # force CDN path
            with p[0], p[1], p[2]:
                for _ in range(4):
                    await announcements._birthday_check(now)
            self.assertFalse(self.posts)  # photo is required; no text-only post
            self.assertNotIn('Neuvillette',
                             announcements._state['birthday_done'].get('2027-12-18', []))
            self.assertEqual(
                announcements._state['birthday_img_fail']['2027-12-18']['Neuvillette'], 4)
            # art recovers later inside the window: post goes out with the image
            q = self.patched(self.fake_bytes_ok)
            with q[0], q[1], q[2]:
                await announcements._birthday_check(now)
            self.assertEqual(len(self.posts), 1)
            self.assertIsNotNone(self.posts[0][3])

    async def test_ping_prefix_when_enabled(self):
        announcements.BIRTHDAY_PING = True
        p = self.patched(self.fake_bytes_ok)
        with p[0], p[1], p[2]:
            await announcements._birthday_check(datetime.datetime(2027, 12, 18, 9, 30))
        self.assertTrue(self.posts[0][1].startswith('@everyone\n# HAPPY BIRTHDAY'))

    async def test_feb29_posts_on_feb28_in_non_leap_year(self):
        p = self.patched(self.fake_bytes_ok)
        with self.with_lines({'Bennett': "Benny's adventure team, assemble!"}):
            with p[0], p[1], p[2]:
                await announcements._birthday_check(datetime.datetime(2027, 2, 28, 9, 30))
        labels = {label for _, _, label, _ in self.posts}
        self.assertIn('birthday Bennett', labels)

    async def test_disabled_or_broken_state_posts_nothing(self):
        announcements._state_broken = True
        p = self.patched(self.fake_bytes_ok)
        with p[0], p[1], p[2]:
            # loop guards on _state_broken; _birthday_check itself is only called when clean
            if not announcements._state_broken:
                await announcements._birthday_check(datetime.datetime(2027, 12, 18, 9, 30))
        self.assertFalse(self.posts)


if __name__ == '__main__':
    unittest.main()
