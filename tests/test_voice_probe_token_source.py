"""Existing credential source compatibility; synthetic secrets only."""
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import test_voice_probe as original
probe = original.probe


class TokenSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        self.boot = self.home / '.termux/boot/start-shouko.sh'
        self.boot.parent.mkdir(parents=True)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.homepatch = patch.object(pathlib.Path, 'home', return_value=self.home)
        self.homepatch.start()
        self.addCleanup(self.homepatch.stop)
        self.dotenv = patch.object(probe, 'load_dotenv')
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)

    def test_reads_only_literal_documented_export_without_running_script(self):
        self.boot.write_text('#!/bin/sh\nexport DISCORD_TOKEN="SYNTHETIC_TOKEN"\nexit 99\npython bot.py\n')
        with patch.object(probe.asyncio, 'create_subprocess_exec') as execute:
            self.assertEqual(probe.existing_token(), 'SYNTHETIC_TOKEN')
        execute.assert_not_called()
        self.assertNotIn('DISCORD_TOKEN', os.environ)

    def test_environment_wins_over_boot(self):
        os.environ['DISCORD_TOKEN'] = 'SYNTHETIC_ENV'
        self.boot.write_text('export DISCORD_TOKEN="SYNTHETIC_BOOT"')
        self.assertEqual(probe.existing_token(), 'SYNTHETIC_ENV')

    def test_rejects_dynamic_chained_or_ambiguous_assignment(self):
        cases = ('export DISCORD_TOKEN=$(cat secret)',
                 'export DISCORD_TOKEN=`cat secret`',
                 'export DISCORD_TOKEN="one"; python bot.py',
                 'export DISCORD_TOKEN="one"\nexport DISCORD_TOKEN="two"',
                 'export DISCORD_TOKEN=""',
                 'export DISCORD_TOKEN="two words"')
        for script in cases:
            self.boot.write_text(script)
            with self.subTest(script=script), self.assertRaises(probe.ProbeError):
                probe.existing_token()

    def test_no_source_has_fixed_code_no_secret_request(self):
        with self.assertRaises(probe.ProbeError) as error:
            probe.existing_token()
        self.assertEqual(str(error.exception), 'EXISTING_TOKEN_SOURCE_NOT_FOUND')

    def test_no_other_assignment_is_imported(self):
        self.boot.write_text('export GEMINI_API_KEY="SYNTHETIC_UNRELATED"\nexport DISCORD_TOKEN=SYNTHETIC_TOKEN\n')
        self.assertEqual(probe.existing_token(), 'SYNTHETIC_TOKEN')
        self.assertNotIn('GEMINI_API_KEY', os.environ)
