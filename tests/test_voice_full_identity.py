"""Local full-persona data loader and memory setup, no Discord/Google calls."""
import pathlib
import tempfile
import unittest
from voice_live.identity import local_persona, VOICE_RULES
from voice_live.gemini import LiveError
from voice_live.memory_session import SessionMemory, MemoryDuplexTurn
from voice_live.memory import digest
from voice_live.memory import Selection

class FullIdentity(unittest.TestCase):
    def test_loads_exact_local_constant_without_executing_other_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'persona.py'
            path.write_text("raise RuntimeError('MUST_NOT_EXECUTE')\nSYSTEM_PROMPT = 'LOCAL_TEST_IDENTITY'\n")
            self.assertEqual(local_persona(path),'LOCAL_TEST_IDENTITY\n\n'+VOICE_RULES)
            path.write_text("SYSTEM_PROMPT = 'PHONE_EDITED_IDENTITY'\n")
            self.assertTrue(local_persona(path).startswith('PHONE_EDITED_IDENTITY'))

    def test_invalid_or_dynamic_persona_fails_closed_no_raw_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'persona.py'
            for content in ("SYSTEM_PROMPT = open('secret').read()",'INVALID_PRIVATE_TEXT @',"SYSTEM_PROMPT = ''",'x'*65537):
                path.write_text(content)
                with self.assertRaisesRegex(LiveError,'^LOCAL_PERSONA_INVALID$'):local_persona(path)

    def test_full_persona_stays_in_setup_not_extraction_or_status(self):
        memory=SessionMemory(selection=Selection(123,456,0,(digest('Likes puzzles'),)),
            requester=123,guild=456,generation=0,
            facts=[{'t':'Likes puzzles','ts':1}],persona='LOCAL_TEST_IDENTITY')
        turn=MemoryDuplexTurn('SYNTHETIC',lambda:True,memory)
        config=turn.setup()
        self.assertEqual(config['setup']['systemInstruction']['parts'][0]['text'],'LOCAL_TEST_IDENTITY')
        self.assertIn('Likes puzzles',str(config))
        self.assertNotIn('maxOutputTokens',config['setup']['generationConfig'])
        self.assertEqual(config['setup']['model'],'models/gemini-3.1-flash-live-preview')
        self.assertIn('Sulafat',str(config))
