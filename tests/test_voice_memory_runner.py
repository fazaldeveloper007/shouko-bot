import asyncio
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

import diary
from voice_live import duplex as runner
from voice_live.member_store import MemberStore
from voice_live.memory import digest
from voice_live.memory_review import private_json, load_selection
from voice_live.timing import Timings


class MemoryRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, worker_code='IDLE_LIMIT', answer='START', change_generation=False,
                       extractor_error=False, confirm='SAVE', automatic=False):
        from test_voice_live import RunnerGates
        guild = RunnerGates().guild()
        callbacks = {}
        def event(fn):
            callbacks[fn.__name__] = fn
            return fn
        async def start(*args, **kwargs):
            await callbacks['on_ready']()
        client = types.SimpleNamespace(event=event, start=start, close=AsyncMock(),
            user=types.SimpleNamespace(id=99), guilds=[guild])
        process = types.SimpleNamespace(wait=AsyncMock(return_value=0), returncode=0)
        bridge = types.SimpleNamespace(p=None, state='ready',
            result=asyncio.get_running_loop().create_future(), safe=lambda: True,
            write=AsyncMock(), timings=Timings())
        async def disconnect(**kwargs):
            if not bridge.result.done():
                bridge.result.set_result({'code': worker_code})
            return True
        bridge.disconnect = disconnect
        console_count = 0
        async def console():
            nonlocal console_count
            console_count += 1
            if console_count == 1:
                return answer
            if console_count == 2:
                await asyncio.Event().wait()  # live STOP task, canceled at shutdown
            return confirm
        async def wait(tasks, **kwargs):
            # Run the fake voice session, then indicate its worker idle/error.
            await tasks[0]
            return {tasks[3]}, set()
        async def connect(*args, **kwargs):
            pass
        with tempfile.TemporaryDirectory() as temp:
            store = MemberStore(temp)
            store.merge_reviewed(42, expected_generation=0, facts=('Likes puzzles',), merge=diary.merge)
            selection_path = Path(temp)/'selection.json'
            private_json(selection_path, {'owner':42, 'guild':guild.id, 'generation':0,
                                          'digests':[digest('Likes puzzles')]})
            async def exchange(b, key, memory):
                self.assertIsNotNone(memory)
                memory.server_event({'serverContent': {'inputTranscription': {'text':'I like chess.'}}}, safe=True)
                if change_generation:
                    store.revoke(42)
                b.result.set_result({'code':worker_code})
            sender = AsyncMock(return_value=json.dumps({'facts':[{'text':'Likes chess',
                'category':'interest', 'source_turn':1, 'source_quote':'I like chess.'}]}))
            if extractor_error:
                sender.side_effect = RuntimeError('private synthetic error')
            with patch.object(diary, 'MEMORY_DIR', Path(temp)), \
                 patch.object(runner.sys.stdin,'isatty',return_value=True), \
                 patch.object(runner,'existing_token',return_value='SYNTHETIC'), \
                 patch.object(runner,'existing_gemini_key',return_value='SYNTHETIC'), \
                 patch.object(runner.discord,'Client',return_value=client), \
                 patch.object(runner.asyncio,'create_subprocess_exec',AsyncMock(return_value=process)), \
                 patch.object(runner,'console_line',console), \
                 patch.object(runner,'LiveBridge',return_value=bridge), \
                 patch.object(runner,'exchange',exchange), \
                 patch.object(runner.asyncio,'wait',wait), \
                 patch.object(runner.discord.VoiceChannel,'connect',connect), \
                 patch.object(runner,'memory_extract_once',sender), \
                 patch('voice_live.automatic_diary.OWNER',42), \
                 patch('voice_live.automatic_diary.GUILD',guild.id), \
                 patch('builtins.print') as output:
                await runner.run(42, guild.id, True, None if automatic else selection_path, automatic_diary=automatic)
            printed = str(output.call_args_list)
            # Only private post-session review can show candidate notes. No raw
            # transcript event or extraction errors are ever printed.
            self.assertNotIn('private synthetic error', printed)
            if answer != 'START' or change_generation or worker_code == 'RECEIVE_ERROR':
                sender.assert_not_awaited()
                self.assertEqual(len(store.snapshot(42)[1]['facts']), 1)
            elif confirm != 'SAVE':
                self.assertEqual(sender.await_count, 1)
                self.assertIn('MEMORY_NOT_SAVED_REVIEW', printed)
                self.assertEqual(len(store.snapshot(42)[1]['facts']), 1)
            elif extractor_error:
                self.assertEqual(sender.await_count, 1)
                self.assertIn('MEMORY_EXTRACTION_FAILED', printed)
                self.assertNotIn('MEMORY_SAVED', printed)
            else:
                self.assertEqual(sender.await_count, 1)
                self.assertIn('MEMORY_SAVED', printed)
                self.assertIn('Self-note: I like chess.', [x['t'] for x in store.snapshot(42)[1]['facts']])
                if automatic:
                    from voice_live.automatic_diary import authorize
                    # Scope constants restored outside patches; validate own store result directly.
                    self.assertTrue((Path(temp)/f'voice-automatic-42-{guild.id}.json').exists())
                else:
                    self.assertIn(digest('Self-note: I like chess.'), load_selection(selection_path).digests)
                self.assertIn('LEAVE_CONFIRMED', printed)
                self.assertIn('LIVE_TIMINGS', printed)
            client.close.assert_awaited_once()

    async def test_safe_idle_saves_once_after_cleanup_updates_selection(self):
        await self.exercise()

    async def test_automatic_own_diary_safe_save_without_picker(self):
        await self.exercise(automatic=True)

    async def test_automatic_own_diary_revoke_stops_save(self):
        await self.exercise(automatic=True,change_generation=True)

    async def test_receive_error_no_extraction(self):
        await self.exercise(worker_code='RECEIVE_ERROR')

    async def test_revocation_no_extraction(self):
        await self.exercise(change_generation=True)

    async def test_cancel_before_start_no_extraction(self):
        await self.exercise(answer='CANCEL')

    async def test_extractor_failure_no_retry_false_success(self):
        await self.exercise(extractor_error=True)

    async def test_unconfirmed_candidates_never_saved(self):
        await self.exercise(confirm='DISCARD')

    async def test_scope_mismatch_before_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'selection.json'
            private_json(path, {'owner':42,'guild':456,'generation':0,'digests':[]})
            with patch.object(runner, 'existing_token') as token:
                with self.assertRaises(Exception):
                    await runner.run(43, 456, True, path)
                token.assert_not_called()


if __name__ == '__main__':
    unittest.main()
