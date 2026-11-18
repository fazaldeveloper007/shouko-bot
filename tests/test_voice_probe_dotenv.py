"""Match the normal bot's dotenv discovery, including parent .env files."""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import test_voice_probe as original
probe = original.probe


class DotenvCompatibility(unittest.TestCase):
    def test_parent_file_matches_real_bot_load_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            root = home / 'shouko-bot'
            root.mkdir()
            (home / '.env').write_text('DISCORD_TOKEN=SYNTHETIC_PARENT_TOKEN\n')
            # Real library call made from a bot.py-style file, not a mock.
            script = root / 'bot.py'
            script.write_text('from dotenv import load_dotenv\nimport os\nload_dotenv()\nassert os.environ.get("DISCORD_TOKEN")=="SYNTHETIC_PARENT_TOKEN"\n')
            env = {k: v for k, v in os.environ.items() if k not in ('DISCORD_TOKEN', 'PYTHON_DOTENV_DISABLED')}
            subprocess.run([sys.executable, str(script)], env=env, check=True, capture_output=True)
            with patch.object(probe, 'ROOT', root), patch.dict(os.environ, env, clear=True):
                self.assertEqual(probe.normal_bot_dotenv(), home / '.env')
                self.assertEqual(probe.existing_token(), 'SYNTHETIC_PARENT_TOKEN')

    def test_nearest_file_wins_even_if_it_has_no_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            root = home / 'shouko-bot'
            root.mkdir()
            (home / '.env').write_text('DISCORD_TOKEN=SYNTHETIC_PARENT_TOKEN\n')
            (root / '.env').write_text('BOT_NAME=Kristy\n')
            with patch.object(probe, 'ROOT', root):
                self.assertEqual(probe.normal_bot_dotenv(), root / '.env')

    def test_repo_file_not_probe_subdirectory_or_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            nested = root / 'voice_probe'
            nested.mkdir()
            (root / '.env').write_text('DISCORD_TOKEN=SYNTHETIC_REPO_TOKEN\n')
            (nested / '.env').write_text('DISCORD_TOKEN=SYNTHETIC_WRONG_TOKEN\n')
            with patch.object(probe, 'ROOT', root):
                self.assertEqual(probe.normal_bot_dotenv(), root / '.env')
