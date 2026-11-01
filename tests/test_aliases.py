"""Nickname registry (aliases.py): server-scoped, explicit, never retargeted."""
import os
import tempfile
import unittest
import pathlib

_tmp = tempfile.TemporaryDirectory()
os.environ["ALIASES_FILE"] = str(pathlib.Path(_tmp.name) / "aliases.json")

import aliases  # noqa: E402


class AliasRegistry(unittest.TestCase):
    def setUp(self):
        p = os.environ["ALIASES_FILE"]
        aliases._ALIASES_FILE = pathlib.Path(p)
        if os.path.exists(p):
            os.unlink(p)

    def test_teach_and_resolve_case_insensitive(self):
        self.assertEqual(aliases.set_alias(10, "Mayank", 111, by_id=999), ("saved", None))
        self.assertEqual(aliases.resolve(10, "MAYANK"), 111)
        self.assertEqual(aliases.resolve(10, "mayank"), 111)

    def test_persists_to_disk(self):
        aliases.set_alias(10, "ug bhau", 222)
        self.assertTrue(os.path.exists(os.environ["ALIASES_FILE"]))
        self.assertEqual(aliases.list_aliases(10), [("ug bhau", 222)])

    def test_taken_alias_never_retarged_silently(self):
        aliases.set_alias(10, "bhai", 111)
        self.assertEqual(aliases.set_alias(10, "BHAI", 222), ("taken", 111))
        self.assertEqual(aliases.resolve(10, "bhai"), 111)  # unchanged

    def test_same_member_reteach_refreshes(self):
        aliases.set_alias(10, "bhai", 111)
        self.assertEqual(aliases.set_alias(10, "bhai", 111), ("saved", None))
        self.assertEqual(aliases.resolve(10, "bhai"), 111)

    def test_guild_isolation(self):
        aliases.set_alias(10, "bhai", 111)
        self.assertIsNone(aliases.resolve(20, "bhai"))

    def test_clean_alias_strips_markup_and_controls(self):
        self.assertEqual(aliases.clean_alias("<@123> boss"), "123 boss")
        self.assertEqual(aliases.clean_alias("@everyone"), "everyone")
        self.assertEqual(aliases.clean_alias("a"), "")
        self.assertEqual(aliases.clean_alias("!!--"), "")
        self.assertEqual(aliases.clean_alias("x" * 40), "")
        self.assertEqual(aliases.clean_alias("evil\nname"), "evil name")

    def test_remove_alias(self):
        aliases.set_alias(10, "bhai", 111)
        self.assertTrue(aliases.remove_alias(10, "BHAI"))
        self.assertIsNone(aliases.resolve(10, "bhai"))
        self.assertFalse(aliases.remove_alias(10, "bhai"))

    def test_cap_enforced(self):
        old = aliases.MAX_ALIASES_PER_GUILD
        aliases.MAX_ALIASES_PER_GUILD = 2
        try:
            aliases.set_alias(10, "a1", 1)
            aliases.set_alias(10, "a2", 2)
            self.assertEqual(aliases.set_alias(10, "a3", 3), ("full", None))
        finally:
            aliases.MAX_ALIASES_PER_GUILD = old

    def test_corrupt_file_fails_open(self):
        with open(os.environ["ALIASES_FILE"], "w") as f:
            f.write("{not json")
        self.assertIsNone(aliases.resolve(10, "bhai"))
        self.assertEqual(aliases.set_alias(10, "bhai", 111), ("saved", None))
        self.assertEqual(aliases.resolve(10, "bhai"), 111)


if __name__ == "__main__":
    unittest.main()
