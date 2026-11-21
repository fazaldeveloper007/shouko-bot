"""Operation labels on API-call log lines: every Gemini/Groq/Tavily
attempt/complete/failed/usage line says what the call is FOR, per async task,
without leaking message text or crossing between concurrent operations."""
import asyncio
import unittest

import retrying
from test_retrying import Session, Response


class CleanNameTests(unittest.TestCase):
    def test_strips_control_chars(self):
        self.assertEqual(retrying.clean_name("a\nb\tc"), "a b c")

    def test_bounds_length(self):
        self.assertEqual(len(retrying.clean_name("x" * 200)), 32)

    def test_never_empty(self):
        self.assertEqual(retrying.clean_name(""), "?")
        self.assertEqual(retrying.clean_name(None), "?")


class OpContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        retrying.set_op("")  # never leak a label into another test's context

    async def test_suffix_empty_by_default(self):
        self.assertEqual(retrying.op_suffix(), "")

    async def test_set_and_reset(self):
        tok = retrying.set_op('replying to Ash in Gaming Impact')
        self.assertEqual(retrying.op_suffix(), ' op="replying to Ash in Gaming Impact"')
        retrying.reset_op(tok)
        self.assertEqual(retrying.op_suffix(), "")

    async def test_concurrent_tasks_keep_their_own_label(self):
        async def tagged(name, seen):
            retrying.set_op(name)
            await asyncio.sleep(0.02)  # force interleaving
            seen.append((name, retrying.op_suffix()))
        seen = []
        await asyncio.gather(tagged("op A", seen), tagged("op B", seen))
        self.assertEqual(sorted(seen), [("op A", ' op="op A"'), ("op B", ' op="op B"')])

    async def test_log_lines_carry_the_label(self):
        retrying.set_op("replying to Ash in Gaming Impact")
        try:
            s = Session([Response(200, {"ok": True})])
            with self.assertLogs("kristy.retry", level="INFO") as cm:
                await retrying.post_json(s, "https://test", label="Gemini",
                                         headers={}, payload={})
            text = "\n".join(cm.output)
            self.assertIn('op="replying to Ash in Gaming Impact"', text)
            self.assertIn("call attempt=1/3", text)
            self.assertIn("complete attempt=1/3 status=200", text)
        finally:
            retrying.set_op("")

    async def test_failed_line_carries_the_label(self):
        retrying.set_op("proactive check in #gaming-chat in Gaming Impact")
        try:
            s = Session([Response(503), Response(503), Response(503)])
            with self.assertLogs("kristy.retry", level="WARNING") as cm:
                with self.assertRaises(Exception):
                    await retrying.post_json(s, "https://test", label="Gemini",
                                             headers={}, payload={}, timeout=1)
            self.assertIn('op="proactive check in #gaming-chat in Gaming Impact"',
                          "\n".join(cm.output))
        finally:
            retrying.set_op("")

    async def test_unlabeled_calls_log_like_before(self):
        s = Session([Response(200, {"ok": True})])
        with self.assertLogs("kristy.retry", level="INFO") as cm:
            await retrying.post_json(s, "https://test", label="Gemini",
                                     headers={}, payload={})
        self.assertNotIn("op=", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
