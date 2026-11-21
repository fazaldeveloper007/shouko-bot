"""Gif intent routing: requests send, discussion/stop never sends, the brain
decides only ambiguous cases through a validated marker (offline).

Regression source: a member asked "Why did u used neuvillette gif as on my
reply?" and Kristy answered with the canned delivery line plus ANOTHER
Neuvillette gif, because the code matched "gif" + "neuvillette" without
reading the intent.
"""
import importlib.util
import os
import pathlib
import tempfile
import unittest
import warnings
from unittest.mock import patch

ROOT = pathlib.Path(__file__).parents[1]


def load_bot():
    env = {'DISCORD_TOKEN': 'OFFLINE_DUMMY', 'GEMINI_API_KEY': 'OFFLINE_DUMMY',
           'MUSIC_ENABLED': '0', 'MUSIC_SYNC_COMMANDS': '0'}
    with tempfile.TemporaryDirectory() as tmp:
        env['MEMORY_DIR'] = tmp
        with patch.dict(os.environ, env), warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=DeprecationWarning)
            spec = importlib.util.spec_from_file_location('offline_bot_gif', ROOT / 'bot.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module


bot = load_bot()
CH = 4242


class ClearRequestTests(unittest.TestCase):
    """Direct asks keep the zero-API short-circuit: the feature stays."""

    def plan(self, text):
        return bot.plan_gif(text, CH)

    def test_send_verb(self):
        self.assertEqual(self.plan("send a neuvillette gif"), ("request", "neuvillette"))

    def test_bare_noun_phrase(self):
        self.assertEqual(self.plan("neuvillette gif"), ("request", "neuvillette"))

    def test_hinglish_imperative(self):
        self.assertEqual(self.plan("furina gif bhejo na"), ("request", "furina"))

    def test_chahiye(self):
        self.assertEqual(self.plan("mujhe raiden gif chahiye"), ("request", "raiden_shogun"))

    def test_polite_question_form(self):
        self.assertEqual(self.plan("can you send me a furina gif please?"),
                         ("request", "furina"))

    def test_deictic_with_request_verb_is_still_a_request(self):
        self.assertEqual(self.plan("neuvillette ki woh gif bhej do na"),
                         ("request", "neuvillette"))

    def test_what_about_is_an_offer_to_send(self):
        self.assertEqual(self.plan("what about a neuvillette gif?"),
                         ("request", "neuvillette"))


class SuppressedTalkTests(unittest.TestCase):
    """Talking ABOUT gifs (or stopping them) must never trigger a send."""

    def plan(self, text):
        return bot.plan_gif(text, CH)

    def test_screenshot_regression_why_did_you_use(self):
        # the exact failing line from the user's screenshot
        self.assertEqual(self.plan("Why did u used neuvillette gif as on my reply?"),
                         ("none", None))

    def test_kyun_bheji(self):
        self.assertEqual(self.plan("Neuvillette GIF kyun bheji?"), ("none", None))

    def test_negation_with_gif(self):
        self.assertEqual(self.plan("mat bhejo furina gif"), ("none", None))

    def test_negation_without_gif_word(self):
        self.assertEqual(self.plan("mat bhejo furina"), ("none", None))

    def test_stop_sending(self):
        self.assertEqual(self.plan("stop sending gifs yaar"), ("none", None))

    def test_deictic_specific_gif(self):
        self.assertEqual(self.plan("that neuvillette gif was so funny"), ("none", None))

    def test_where_from_question(self):
        self.assertEqual(self.plan("ye gif kahan se liya?"), ("none", None))

    def test_past_tense_narration(self):
        self.assertEqual(self.plan("kal usne mujhe raiden gif bheja tha"), ("none", None))

    def test_dont_send(self):
        self.assertEqual(self.plan("don't send furina gif"), ("none", None))

    def test_quoted_request_is_not_theirs(self):
        self.assertEqual(
            self.plan('usne kaha "neuvillette gif bhej do" aur phir chali gayi'),
            ("none", None))

    def test_suppression_beats_mood_category(self):
        # "lol" would normally ride a laugh gif; gif discussion vetoes it
        self.assertEqual(self.plan("lol why did you send that gif"), ("none", None))

    def test_bare_negation_unrelated_to_gifs_stays_open(self):
        self.assertFalse(bot.gif_talk_suppressed("mat karo yaar, ganyu mat khelo"))


class MixedNegationTests(unittest.TestCase):
    """A negated alias names who they DON'T want; the request survives."""

    def test_not_x_send_y(self):
        self.assertEqual(bot.plan_gif("not furina, send a neuvillette gif", CH),
                         ("request", "neuvillette"))

    def test_x_nahi_y_bhejo(self):
        self.assertEqual(bot.plan_gif("furina nahi, neuvillette gif bhejo", CH),
                         ("request", "neuvillette"))

    def test_negated_only_character_leaves_no_request(self):
        self.assertEqual(bot.plan_gif("not furina gif yaar", CH), ("none", None))


class PreservedBehaviourTests(unittest.TestCase):

    def test_short_hype_still_zero_api(self):
        self.assertEqual(bot.plan_gif("NEUVILLETTE!!", CH), ("hype", "neuvillette"))
        self.assertEqual(bot.plan_gif("furina my queen", CH), ("hype", "furina"))

    def test_mention_ride_with_cooldown(self):
        text = "guys kal maine neuvillette finally build kar liya, ratio dekhna"
        self.assertEqual(bot.plan_gif(text, CH, now=1000.0), ("mention", "neuvillette"))
        # inside the cooldown the mention ride is gone, nothing else matches
        bot._last_mention_gif[CH] = 1000.0
        try:
            self.assertEqual(bot.plan_gif(text, CH, now=1001.0), ("none", None))
        finally:
            bot._last_mention_gif.pop(CH, None)

    def test_mood_category_preserved(self):
        self.assertEqual(bot.plan_gif("lol that's hilarious", CH), ("category", "laugh"))

    def test_greeting_mood_preserved(self):
        self.assertEqual(bot.plan_gif("hello kristy", CH), ("category", "hello"))

    def test_gif_word_without_character_stays_code_quiet(self):
        # no expansion: an unnamed gif ask is for the brain to talk through,
        # never a code-chosen random gif
        self.assertEqual(bot.plan_gif("koi funny gif bhejo", CH), ("none", None))


class MarkerValidationTests(unittest.TestCase):
    """The brain may ask for a gif via [[gif:alias]]; code is the executor and
    only honours a marker the member's own words support."""

    def test_valid_marker_on_real_request(self):
        key, reply = bot.extract_gif_marker("[[gif:neuvillette]] ye lo! ✨",
                                            "woh neuvillette gif bhej do na")
        self.assertEqual(key, "neuvillette")
        self.assertEqual(reply, "ye lo! ✨")

    def test_marker_target_grounded_on_why_question(self):
        # intent on a why-question is the brain's call (it should not mark);
        # if it does, the TARGET is at least grounded in the member's words
        key, reply = bot.extract_gif_marker("[[gif:neuvillette]] ye lo!",
                                            "Why did u used neuvillette gif?")
        self.assertEqual(key, "neuvillette")
        self.assertNotIn("[[", reply)

    def test_which_request_recovers_via_brain(self):
        # "which" is conversation-shaped but a genuine ask: code stays quiet,
        # the brain's marker validates and code sends
        self.assertEqual(bot.plan_gif("which furina gif can you send me?", CH), ("none", None))
        key, _ = bot.extract_gif_marker("[[gif:furina]] ye rahi!",
                                        "which furina gif can you send me?")
        self.assertEqual(key, "furina")

    def test_deictic_target_from_replied_message(self):
        key, _ = bot.extract_gif_marker(
            "[[gif:neuvillette]] lo ji",
            "send that one",
            ref_text="Kristy (bot): ye le [image: neuvillette gif]")
        self.assertEqual(key, "neuvillette")

    def test_deictic_target_from_just_sent_gif(self):
        key, _ = bot.extract_gif_marker(
            "[[gif:neuvillette]] ek aur!",
            "woh wali ek aur bhejo na",
            last_sent=("neuvillette", bot.time.monotonic()))
        self.assertEqual(key, "neuvillette")

    def test_stale_last_sent_not_a_target(self):
        key, _ = bot.extract_gif_marker(
            "[[gif:neuvillette]] ek aur!",
            "woh wali ek aur bhejo na",
            last_sent=("neuvillette", bot.time.monotonic() - 7200))
        self.assertIsNone(key)

    def test_marker_rejected_on_stop(self):
        key, _ = bot.extract_gif_marker("[[gif:furina]] ok", "mat bhejo furina gif")
        self.assertIsNone(key)

    def test_marker_rejected_for_unnamed_character(self):
        key, _ = bot.extract_gif_marker("[[gif:furina]] ye lo",
                                        "send a neuvillette gif")
        self.assertIsNone(key)

    def test_marker_rejected_without_gif_word(self):
        key, _ = bot.extract_gif_marker("[[gif:neuvillette]] ye lo",
                                        "neuvillette bhej do na")
        self.assertIsNone(key)

    def test_marker_rejected_for_unknown_alias(self):
        key, reply = bot.extract_gif_marker("[[gif:spamton]] ok", "send a spamton gif")
        self.assertIsNone(key)
        self.assertEqual(reply, "ok")

    def test_all_markers_stripped_even_when_invalid(self):
        key, reply = bot.extract_gif_marker("[[gif:furina]] lo [[gif:neuvillette]]",
                                            "send furina gif and neuvillette gif")
        self.assertEqual(key, "furina")
        self.assertEqual(reply, "lo")

    def test_no_marker_passes_through(self):
        key, reply = bot.extract_gif_marker("plain answer", "why that gif?")
        self.assertIsNone(key)
        self.assertEqual(reply, "plain answer")


if __name__ == "__main__":
    unittest.main()
