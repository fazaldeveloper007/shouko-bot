import importlib.util
import os
import pathlib
import unittest
from unittest.mock import AsyncMock, patch
from test_bot_music_integration import load_source

ROOT = pathlib.Path(__file__).parents[1]
with patch.dict(os.environ, {'DISCORD_TOKEN': 'OFFLINE_DUMMY', 'GEMINI_API_KEY': 'OFFLINE_DUMMY', 'MUSIC_ENABLED': '0', 'MUSIC_SYNC_COMMANDS': '0'}):
    os.environ.pop('GEMINI_MODEL', None)
    _spec = importlib.util.spec_from_file_location('offline_bot_neon', ROOT / 'bot.py')
    bot = importlib.util.module_from_spec(_spec)
    load_source(_spec, bot)

NEON = "https://generativelanguage.googleapis.com/v1beta/models/gemini-4-neon:generateContent"


class NeonCompat(unittest.IsolatedAsyncioTestCase):
    def test_default_model_payload_is_untouched(self):
        p = {"generationConfig": {"maxOutputTokens": 80, "temperature": 0.7}}
        self.assertIs(bot.gemini_payload_for_model(p, bot.GEMINI_URL), p)
        self.assertEqual(bot.GEMINI_MODEL, "gemini-3.5-flash-lite")

    def test_neon_adjusts_copy_only(self):
        p = {"generationConfig": {"maxOutputTokens": 80, "temperature": 0.7, "topP": 1}}
        out = bot.gemini_payload_for_model(p, NEON)
        cfg = out["generationConfig"]
        self.assertNotIn("temperature", cfg)
        self.assertNotIn("topP", cfg)
        self.assertEqual(cfg["thinkingConfig"], {"thinkingLevel": "low"})
        self.assertEqual(cfg["maxOutputTokens"], 8192)
        self.assertEqual(p["generationConfig"]["temperature"], 0.7)

    def test_neon_keeps_explicit_level_and_larger_cap(self):
        p = {"generationConfig": {"maxOutputTokens": 20000, "thinkingConfig": {"thinkingLevel": "medium"}}}
        cfg = bot.gemini_payload_for_model(p, NEON)["generationConfig"]
        self.assertEqual(cfg["thinkingConfig"]["thinkingLevel"], "medium")
        self.assertEqual(cfg["maxOutputTokens"], 20000)

    def test_gemini_38_flash_opt_in_adjustments(self):
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
        p = {"generationConfig": {"maxOutputTokens": 80, "temperature": 0.7, "thinkingConfig": {"thinkingLevel": "minimal"}}}
        cfg = bot.gemini_payload_for_model(p, url)["generationConfig"]
        self.assertNotIn("temperature", cfg)
        self.assertEqual(cfg["thinkingConfig"]["thinkingLevel"], "low")
        self.assertEqual(cfg["maxOutputTokens"], 1024)

    def test_other_models_not_matched(self):
        for m in ("gemini-3.5-flash-lite", "gemini-3.8-flash-lite", "gemini-4-neon-x", "gemini-3.8-live"):
            u = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
            p = {"generationConfig": {"temperature": .5}}
            self.assertIs(bot.gemini_payload_for_model(p, u), p, m)

    def test_neon_replaces_rejected_minimal(self):
        p = {"generationConfig": {"thinkingConfig": {"thinkingLevel": "minimal"}}}
        cfg = bot.gemini_payload_for_model(p, NEON)["generationConfig"]
        self.assertEqual(cfg["thinkingConfig"]["thinkingLevel"], "low")

    async def test_gemini_generate_sends_adjusted_payload_with_fake_http(self):
        fake = AsyncMock(return_value={})
        with patch.object(bot, "groq_session", AsyncMock(return_value=object())), \
             patch.object(bot.retrying, "post_json", fake):
            await bot.gemini_generate({"generationConfig": {"temperature": .9}}, url=NEON)
        sent = fake.call_args.kwargs["payload"]["generationConfig"]
        self.assertNotIn("temperature", sent)
        self.assertEqual(sent["maxOutputTokens"], 8192)

if __name__ == "__main__":
    unittest.main()
