"""Channel recap (recap.py): buffered overflow, folding, fail-open behavior."""
import asyncio
import os
import pathlib
import tempfile
import unittest

_tmp = tempfile.TemporaryDirectory()

import recap  # noqa: E402


def fresh(preserve_file=False):
    recap.PATH = pathlib.Path(_tmp.name) / "channel-recaps.json"
    recap._cache = None
    recap._running.clear()
    if not preserve_file and recap.PATH.exists():
        recap.PATH.unlink()


class RecapMemory(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_empty_by_default(self):
        self.assertEqual(recap.get(123), "")

    def test_note_buffers_until_due(self):
        due_flags = [recap.note(1, "A", f"line {i}") for i in range(recap.RECAP_EVERY - 1)]
        self.assertFalse(any(due_flags))
        self.assertTrue(recap.note(1, "A", "last line"))

    def test_update_folds_lines_into_recap(self):
        for i in range(recap.RECAP_EVERY):
            recap.note(1, "Fazal", f"msg {i}")

        async def summarize(old, lines):
            self.assertEqual(old, "")
            self.assertEqual(len(lines), recap.RECAP_EVERY)
            return "Fazal talked about msgs 0-11."

        asyncio.run(recap.update(1, summarize))
        self.assertEqual(recap.get(1), "Fazal talked about msgs 0-11.")
        # buffer consumed
        self.assertEqual(recap._load()["1"]["pending"], [])
        # and persisted to disk
        fresh(preserve_file=True)
        self.assertEqual(recap.get(1), "Fazal talked about msgs 0-11.")

    def test_failed_update_keeps_lines(self):
        for i in range(recap.RECAP_EVERY):
            recap.note(1, "A", f"msg {i}")

        async def bad(old, lines):
            raise RuntimeError("api down")

        asyncio.run(recap.update(1, bad))
        self.assertEqual(recap.get(1), "")
        self.assertEqual(len(recap._load()["1"]["pending"]), recap.RECAP_EVERY)

    def test_empty_summary_never_wipes_recap(self):
        recap._load()["1"] = {"recap": "old recap", "pending": [["A", "x"]]}

        async def empty(old, lines):
            return ""

        asyncio.run(recap.update(1, empty))
        self.assertEqual(recap.get(1), "old recap")

    def test_recap_capped(self):
        recap._load()["1"] = {"recap": "", "pending": [["A", "x"]]}

        async def huge(old, lines):
            return "z" * 5000

        asyncio.run(recap.update(1, huge))
        self.assertEqual(len(recap.get(1)), recap.RECAP_MAX_CHARS)

    def test_pending_capped(self):
        for i in range(recap.PENDING_CAP + 10):
            recap.note(1, "A", f"msg {i}")
        self.assertLessEqual(len(recap._load()["1"]["pending"]), recap.PENDING_CAP)

    def test_channels_isolated(self):
        recap._load()["1"] = {"recap": "server one", "pending": []}
        recap._load()["2"] = {"recap": "server two", "pending": []}
        self.assertEqual(recap.get(1), "server one")
        self.assertEqual(recap.get(2), "server two")

    def test_corrupt_file_is_forgotten(self):
        fresh()
        recap.PATH.write_text("not json{{{")
        self.assertEqual(recap.get(1), "")


if __name__ == "__main__":
    unittest.main()
