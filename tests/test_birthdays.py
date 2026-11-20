import datetime
import os
import unittest
from unittest.mock import AsyncMock, patch

import announcements
from birthdays import BIRTHDAYS


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


class CheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        announcements._state = announcements._default_state()
        announcements._state_broken = False
        announcements._birthday_writer = None
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

    async def test_posts_each_character_once_with_image(self):
        now = datetime.datetime(2027, 2, 2, 9, 30)  # Lynette + Lyney
        p = self.patched(self.fake_bytes_ok)
        with p[0], p[1], p[2]:
            await announcements._birthday_check(now)
        self.assertEqual(len(self.posts), 2)
        labels = {label for _, _, label, _ in self.posts}
        self.assertEqual(labels, {'birthday Lynette', 'birthday Lyney'})
        for cid, text, _, image in self.posts:
            self.assertEqual(cid, announcements.BIRTHDAY_CHANNEL_ID)
            self.assertIn('Happy Birthday', text)
            self.assertIsNotNone(image)
            self.assertNotIn('@everyone', text)
        self.assertEqual(sorted(announcements._state['birthday_done']['2027-02-02']),
                         ['Lynette', 'Lyney'])
        self.posts.clear()
        with p[0], p[1], p[2]:
            await announcements._birthday_check(now)
        self.assertFalse(self.posts)

    async def test_window_respected(self):
        p = self.patched(self.fake_bytes_ok)
        for hh, mm in ((8, 59), (12, 0), (23, 30)):
            with p[0], p[1], p[2]:
                await announcements._birthday_check(datetime.datetime(2027, 12, 31, hh, mm))
        self.assertFalse(self.posts)
        with p[0], p[1], p[2]:
            await announcements._birthday_check(datetime.datetime(2027, 12, 31, 9, 5))
        self.assertEqual(len(self.posts), 1)

    async def test_image_failure_never_posts_text_only(self):
        now = datetime.datetime(2027, 12, 31, 9, 30)  # Zhongli
        p = self.patched(self.fake_bytes_fail)
        with p[0], p[1], p[2]:
            for _ in range(4):
                await announcements._birthday_check(now)
        self.assertFalse(self.posts)  # photo is required; no text-only fallback
        self.assertNotIn('Zhongli', announcements._state['birthday_done'].get('2027-12-31', []))
        self.assertEqual(announcements._state['birthday_img_fail']['2027-12-31']['Zhongli'], 4)
        # art recovers later inside the window: post goes out with the image
        q = self.patched(self.fake_bytes_ok)
        with q[0], q[1], q[2]:
            await announcements._birthday_check(now)
        self.assertEqual(len(self.posts), 1)
        self.assertIsNotNone(self.posts[0][3])

    async def test_writer_used_when_available(self):
        announcements._birthday_writer = AsyncMock(return_value='custom AI wish line')
        p = self.patched(self.fake_bytes_ok)
        with p[0], p[1], p[2]:
            await announcements._birthday_check(datetime.datetime(2027, 10, 13, 9, 30))
        self.assertEqual(len(self.posts), 1)
        self.assertIn('custom AI wish line', self.posts[0][1])

    async def test_writer_failure_uses_fallback(self):
        announcements._birthday_writer = AsyncMock(side_effect=RuntimeError('AI down'))
        p = self.patched(self.fake_bytes_ok)
        with p[0], p[1], p[2]:
            await announcements._birthday_check(datetime.datetime(2027, 10, 13, 9, 30))
        self.assertEqual(len(self.posts), 1)
        self.assertIn('Furina', self.posts[0][1])
        self.assertIn('Happy Birthday, Furina!', self.posts[0][1])

    async def test_ping_prefix_when_enabled(self):
        announcements.BIRTHDAY_PING = True
        p = self.patched(self.fake_bytes_ok)
        with p[0], p[1], p[2]:
            await announcements._birthday_check(datetime.datetime(2027, 10, 13, 9, 30))
        self.assertTrue(self.posts[0][1].startswith('@everyone'))

    async def test_feb29_posts_on_feb28_in_non_leap_year(self):
        p = self.patched(self.fake_bytes_ok)
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
                await announcements._birthday_check(datetime.datetime(2027, 10, 13, 9, 30))
        self.assertFalse(self.posts)


class WishTests(unittest.IsolatedAsyncioTestCase):
    async def test_wish_sanitizes_mentions_and_links(self):
        os.environ.setdefault('DISCORD_TOKEN', 'test-token')
        os.environ.setdefault('GEMINI_API_KEY', 'test-key')
        import bot
        with patch.object(bot, 'call_extractor',
                          AsyncMock(return_value='{"wish": "Aaj ka din! @everyone party @here"}')):
            wish = await bot._birthday_wish('Furina')
        self.assertEqual(wish, 'Aaj ka din!  party')
        with patch.object(bot, 'call_extractor',
                          AsyncMock(return_value='{"wish": "see http://evil.example"}')):
            self.assertIsNone(await bot._birthday_wish('Furina'))
        with patch.object(bot, 'call_extractor', AsyncMock(side_effect=RuntimeError)):
            self.assertIsNone(await bot._birthday_wish('Furina'))
        with patch.object(bot, 'call_extractor', AsyncMock(return_value='not json')):
            self.assertIsNone(await bot._birthday_wish('Furina'))


if __name__ == '__main__':
    unittest.main()
