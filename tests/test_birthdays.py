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
        return (b'\x89PNG\r\n\x1a\n-fake', 'image/png')

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


class SniffTests(unittest.TestCase):
    def test_magic_bytes(self):
        sniff = announcements.sniff_image
        self.assertEqual(sniff(b'\x89PNG\r\n\x1a\n' + b'x' * 100), 'png')
        self.assertEqual(sniff(b'\xff\xd8\xff\xe0' + b'x' * 100), 'jpg')
        self.assertEqual(sniff(b'GIF89a' + b'x' * 100), 'gif')
        self.assertEqual(sniff(b'RIFF' + b'\x00' * 4 + b'WEBP' + b'x' * 100), 'webp')
        self.assertIsNone(sniff(b'<html><body>Attention Required</body></html>'))
        self.assertIsNone(sniff(b''))
        self.assertIsNone(sniff(None))
        self.assertIsNone(sniff(b'RIFFtoo-short'))


class NonImageBytesTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_html_200_response_never_posts_then_recovers(self):
        # A 200 carrying an HTML error/challenge page must be treated as a
        # failed load: posting those bytes as event.jpg is what leaves a
        # birthday wish with no visible photo.
        async def fake_bytes_html(url, cap):
            return (b'<html><body>Just a moment...</body></html>', 'text/html')
        now = datetime.datetime(2027, 12, 18, 9, 30)
        with patch.dict(announcements.BIRTHDAY_LOCAL_ART, {}, clear=True):  # force CDN path
            with (patch.object(announcements, '_post', self.fake_post),
                  patch.object(announcements, 'fetch_bytes', fake_bytes_html),
                  patch.object(announcements, 'save_state', lambda: None)):
                for _ in range(3):
                    await announcements._birthday_check(now)
            self.assertFalse(self.posts)
            self.assertNotIn('Neuvillette',
                             announcements._state['birthday_done'].get('2027-12-18', []))
            self.assertEqual(
                announcements._state['birthday_img_fail']['2027-12-18']['Neuvillette'], 3)
            # real image later in the window: posts, named by sniffed type
            async def fake_bytes_ok(url, cap):
                return (b'\x89PNG\r\n\x1a\n' + b'x' * 64, 'text/html')  # wrong header, real bytes
            with (patch.object(announcements, '_post', self.fake_post),
                  patch.object(announcements, 'fetch_bytes', fake_bytes_ok),
                  patch.object(announcements, 'save_state', lambda: None)):
                await announcements._birthday_check(now)
            self.assertEqual(len(self.posts), 1)
            self.assertEqual(self.posts[0][3][1], 'png')  # sniffed, not header


class FakeMessage:
    def __init__(self, attachments):
        self.attachments = attachments
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, message):
        self.message = message
        self.sent = []

    async def send(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return self.message


class PostedMessage:
    """A message already visible in channel history."""
    def __init__(self, author_id, content, attachments, created_at):
        import types
        self.author = types.SimpleNamespace(id=author_id)
        self.content = content
        self.attachments = attachments
        self.created_at = created_at
        self.deleted = False

    async def delete(self):
        self.deleted = True


class RaisingChannel:
    """Channel whose send always fails after possibly delivering, with a
    fixed history for the landed-post check."""
    def __init__(self, history=(), history_error=None):
        self.sent = []
        self._history = list(history)
        self._history_error = history_error

    async def send(self, text, **kwargs):
        self.sent.append((text, kwargs))
        raise RuntimeError('response lost')

    def history(self, limit=5):
        if self._history_error is not None:
            raise self._history_error
        items = self._history[:limit]

        class _It:
            def __aiter__(self):
                async def gen():
                    for item in items:
                        yield item
                return gen()
        return _It()


class AmbiguousSendTests(unittest.IsolatedAsyncioTestCase):
    """A send error after the message may have landed must never cause a
    blind retry (duplicate wish). Only a confirmed deletion of a landed
    photo-less post may retry; every other ambiguous outcome (including a
    history no-match, which cannot prove absence) suppresses the retry."""
    BOT_ID = 999
    PNG = b'\x89PNG\r\n\x1a\n' + b'x' * 64

    def setUp(self):
        import types
        announcements.DRY_RUN = False
        self.client = types.SimpleNamespace(user=types.SimpleNamespace(id=self.BOT_ID))
        self.client_patch = patch.object(announcements, 'client_ref', self.client)
        self.client_patch.start()

    def tearDown(self):
        self.client_patch.stop()

    async def _post_to(self, channel, text='wish text', image=None):
        async def fake_get_channel(channel_id, label):
            return channel
        with patch.object(announcements, '_get_channel', fake_get_channel):
            return await announcements._post(1, text, 'birthday Test', image=image)

    def _msg(self, content='wish text', attachments=('a',), seconds_ago=5, author_id=None):
        created = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
        return PostedMessage(author_id if author_id is not None else self.BOT_ID,
                             content, list(attachments), created)

    async def test_send_error_no_match_not_retried(self):
        # History no-match cannot prove absence (the message may not be
        # visible yet), so the ambiguous outcome suppresses the retry.
        channel = RaisingChannel(history=[])
        self.assertTrue(await self._post_to(channel, image=(self.PNG, 'png')))

    async def test_send_error_landed_with_image_not_retried(self):
        landed = self._msg(attachments=('a',))
        channel = RaisingChannel(history=[landed])
        self.assertTrue(await self._post_to(channel, image=(self.PNG, 'png')))
        self.assertFalse(landed.deleted)

    async def test_send_error_landed_text_only_deleted_and_retried(self):
        landed = self._msg(attachments=[])
        channel = RaisingChannel(history=[landed])
        self.assertFalse(await self._post_to(channel, image=(self.PNG, 'png')))
        self.assertTrue(landed.deleted)

    async def test_send_error_stale_match_not_retried(self):
        # Identical text posted long ago (e.g. daily morning post) is not the
        # message that may have just landed; outcome stays ambiguous, no retry.
        channel = RaisingChannel(history=[self._msg(seconds_ago=600)])
        self.assertTrue(await self._post_to(channel, image=(self.PNG, 'png')))

    async def test_send_error_other_authors_match_not_retried(self):
        channel = RaisingChannel(history=[self._msg(author_id=12345)])
        self.assertTrue(await self._post_to(channel, image=(self.PNG, 'png')))

    async def test_send_error_unverifiable_history_not_retried(self):
        channel = RaisingChannel(history_error=RuntimeError('no history perm'))
        self.assertTrue(await self._post_to(channel, image=(self.PNG, 'png')))

    async def test_send_error_unknown_bot_identity_not_retried(self):
        import types
        with patch.object(announcements, 'client_ref', types.SimpleNamespace(user=None)):
            channel = RaisingChannel(history=[])
            self.assertTrue(await self._post_to(channel, image=(self.PNG, 'png')))

    async def test_send_error_text_post_landed_not_retried(self):
        landed = self._msg(attachments=[])
        channel = RaisingChannel(history=[landed])
        self.assertTrue(await self._post_to(channel, image=None))  # text-only post needs no photo
        self.assertFalse(landed.deleted)


class PostImageVerifyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        announcements.DRY_RUN = False

    async def test_post_with_attachment_confirmed(self):
        channel = FakeChannel(FakeMessage(attachments=['attachment']))
        async def fake_get_channel(channel_id, label):
            return channel
        png = b'\x89PNG\r\n\x1a\n' + b'x' * 64
        with patch.object(announcements, '_get_channel', fake_get_channel):
            ok = await announcements._post(1, 'text', 'birthday Test', image=(png, 'png'))
        self.assertTrue(ok)
        self.assertFalse(channel.message.deleted)
        self.assertIn('file', channel.sent[0][1])

    async def test_post_without_attachment_is_deleted_and_retried(self):
        channel = FakeChannel(FakeMessage(attachments=[]))
        async def fake_get_channel(channel_id, label):
            return channel
        png = b'\x89PNG\r\n\x1a\n' + b'x' * 64
        with patch.object(announcements, '_get_channel', fake_get_channel):
            ok = await announcements._post(1, 'text', 'birthday Test', image=(png, 'png'))
        self.assertFalse(ok)  # caller retries on next tick
        self.assertTrue(channel.message.deleted)

    async def test_delete_failure_not_retried(self):
        class Undeletable(FakeMessage):
            async def delete(self):
                raise RuntimeError('no perms')
        channel = FakeChannel(Undeletable(attachments=[]))
        async def fake_get_channel(channel_id, label):
            return channel
        png = b'\x89PNG\r\n\x1a\n' + b'x' * 64
        with patch.object(announcements, '_get_channel', fake_get_channel):
            ok = await announcements._post(1, 'text', 'birthday Test', image=(png, 'png'))
        self.assertTrue(ok)  # a duplicate wish is worse than one missing photo

    async def test_text_only_post_unaffected(self):
        channel = FakeChannel(FakeMessage(attachments=[]))
        async def fake_get_channel(channel_id, label):
            return channel
        with patch.object(announcements, '_get_channel', fake_get_channel):
            ok = await announcements._post(1, 'text', 'morning')
        self.assertTrue(ok)
        self.assertFalse(channel.message.deleted)


if __name__ == '__main__':
    unittest.main()
