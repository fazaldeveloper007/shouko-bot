"""Read-only diagnostic output excludes source and synthetic secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1] / 'voice_probe/credential_check.py'
SECRET = 'SYNTHETIC_PRIVATE_MARKER_DO_NOT_OUTPUT'


class CredentialCheck(unittest.TestCase):
    def run_case(self, source, dotenv=None, env_token=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            root.mkdir()
            (root / 'voice_probe').mkdir()
            (root / 'voice_probe/receive.py').write_text('')
            (root / 'bot.py').write_text(source)
            if dotenv is not None:
                (root / '.env').write_text(dotenv)
            env = {k: v for k, v in os.environ.items() if k != 'DISCORD_TOKEN'}
            if env_token:
                env['DISCORD_TOKEN'] = SECRET
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            result = subprocess.run([sys.executable, str(HELPER)], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=10, check=True)
            after = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            self.assertNotIn(SECRET, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            return json.loads(result.stdout.splitlines()[0].partition(' ')[2])

    def test_literal_and_presence_without_values(self):
        result = self.run_case('DISCORD_TOKEN="' + SECRET + '"\n', 'DISCORD_TOKEN=' + SECRET, True)
        self.assertEqual(result['bot_token_assignment'], ['top_level:literal_nonempty'])
        self.assertTrue(result['inherited_token_present'])
        self.assertEqual(result['dotenv_candidates'][0]['token_key'], 'nonempty')

    def test_nested_assignment_labeled_not_executed(self):
        result = self.run_case('def f():\n DISCORD_TOKEN="' + SECRET + '"\nraise Exception("' + SECRET + '")\n')
        self.assertEqual(result['bot_token_assignment'], ['nested:literal_nonempty'])

    def test_unparseable_source_no_trace_or_source(self):
        result = self.run_case('DISCORD_TOKEN = "' + SECRET)
        self.assertEqual(result['bot_token_assignment'], 'unreadable_or_unparseable')

    def test_expression_and_dotenv_presence(self):
        result = self.run_case('load_dotenv()\nDISCORD_TOKEN=os.getenv("DISCORD_TOKEN")\n', 'BOT_NAME=Kristy\n')
        self.assertEqual(result['bot_token_assignment'], ['top_level:expression'])
        self.assertTrue(result['bot_bare_load_dotenv_call'])
        self.assertEqual(result['dotenv_candidates'][0]['token_key'], 'absent')
