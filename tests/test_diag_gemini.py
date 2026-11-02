import asyncio
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import diag_gemini

class Diag(unittest.TestCase):
    def run_main(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            asyncio.run(diag_gemini.main())
        return buf.getvalue()
    def test_missing_key_makes_no_request(self):
        with patch.object(diag_gemini, 'GEMINI_API_KEY', ''):
            out = self.run_main()
        self.assertIn('missing', out); self.assertNotIn('models-lookup', out)
    def test_http_line_prints_only_status_and_time(self):
        class Resp:
            status = 403
            async def read(self): return b'secret-body'
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        class Session:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def get(self, url, headers=None): return Resp()
        with patch.object(diag_gemini, 'GEMINI_API_KEY', 'SECRETKEY123'), \
             patch.object(diag_gemini.aiohttp, 'ClientSession', Session):
            out = self.run_main()
        self.assertIn('models-lookup: http=403 time=', out)
        self.assertNotIn('SECRETKEY123', out)
        self.assertNotIn('secret-body', out)
        self.assertNotIn('generativelanguage', out)
    def test_exception_line_prints_class_only(self):
        class Session:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): raise asyncio.TimeoutError()
            async def __aexit__(self, *a): return False
        with patch.object(diag_gemini, 'GEMINI_API_KEY', 'k'), \
             patch.object(diag_gemini.aiohttp, 'ClientSession', Session):
            out = self.run_main()
        self.assertIn('models-lookup: error=TimeoutError time=', out)
