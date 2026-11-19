import unittest
from unittest.mock import patch
from voice_live import duplex
from voice_live.gemini import LiveError

class SourceGate(unittest.IsolatedAsyncioTestCase):
    async def test_affected_routes_fail_before_sources_credentials_or_network(self):
        for options in ({'full_persona':True},{'automatic_diary':True},{'memory_path':'PRIVATE_SOURCE'}):
            with self.subTest(options=options), \
                 patch.object(duplex,'load_selection',side_effect=AssertionError('source read')), \
                 patch.object(duplex,'existing_token',side_effect=AssertionError('credentials')), \
                 patch.object(duplex,'existing_gemini_key',side_effect=AssertionError('credentials')), \
                 patch.object(duplex.discord,'Client',side_effect=AssertionError('network')):
                with self.assertRaisesRegex(LiveError,'^FREE_SOURCE_REVIEW_REQUIRED$'):
                    await duplex.run(42,100,True,**options)
