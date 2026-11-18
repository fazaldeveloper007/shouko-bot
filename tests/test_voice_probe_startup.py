"""Startup diagnostics and cleanup gates, offline only."""
import types
import unittest
from unittest.mock import patch, AsyncMock
import test_voice_probe as original
probe = original.probe


class StartupDiagnostics(unittest.IsolatedAsyncioTestCase):
    def common(self):
        import contextlib
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(probe.sys.stdin, 'isatty', return_value=True))
        stack.enter_context(patch.object(probe, 'load_dotenv'))
        proc = types.SimpleNamespace(wait=AsyncMock(return_value=0))
        stack.enter_context(patch.object(probe.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc)))
        return stack

    async def test_missing_token_is_named_without_login_or_secrets(self):
        stack = self.common()
        stack.enter_context(patch.dict(probe.os.environ, {}, clear=True))
        client = stack.enter_context(patch.object(probe.discord, 'Client'))
        with self.assertRaises(probe.ProbeError) as caught:
            await probe.run(42)
        self.assertEqual(str(caught.exception), 'DISCORD_TOKEN_UNAVAILABLE')
        client.assert_not_called()

    async def test_gateway_failure_prints_no_probe_join_and_closes(self):
        stack = self.common()
        stack.enter_context(patch.dict(probe.os.environ, {'DISCORD_TOKEN': 'SYNTHETIC_PRIVATE_TOKEN'}))
        fake = types.SimpleNamespace(event=lambda f: f, start=AsyncMock(side_effect=RuntimeError('SYNTHETIC_PRIVATE_TOKEN')),
                                     close=AsyncMock())
        stack.enter_context(patch.object(probe.discord, 'Client', return_value=fake))
        output = stack.enter_context(patch('builtins.print'))
        with self.assertRaises(RuntimeError):
            await probe.run(42)
        fake.close.assert_awaited_once()
        printed = ' '.join(str(c) for c in output.call_args_list)
        self.assertIn('PROBE_JOIN_NOT_REQUESTED', printed)
        self.assertNotIn('SYNTHETIC_PRIVATE_TOKEN', printed)

    async def test_start_gate_cancels_before_any_bridge_or_join(self):
        stack = self.common()
        stack.enter_context(patch.dict(probe.os.environ, {'DISCORD_TOKEN': 'SYNTHETIC_PRIVATE_TOKEN'}))
        callbacks = {}
        ch = original.Guards().channel([42])
        ch.guild.id = 100
        def register(fn):
            callbacks[fn.__name__] = fn
            return fn
        async def start(*a, **k):
            await callbacks['on_ready']()
        fake = types.SimpleNamespace(event=register, start=start, close=AsyncMock(),
                                     user=types.SimpleNamespace(id=99), guilds=[ch.guild])
        stack.enter_context(patch.object(probe.discord, 'Client', return_value=fake))
        stack.enter_context(patch.object(probe.discord, 'VoiceChannel', types.SimpleNamespace))
        stack.enter_context(patch.object(probe, 'console_line', AsyncMock(return_value='CANCEL')))
        bridge = stack.enter_context(patch.object(probe, 'ProbeBridge'))
        output = stack.enter_context(patch('builtins.print'))
        await probe.run(42)
        bridge.assert_not_called()
        self.assertIn('PROBE_JOIN_NOT_REQUESTED', ' '.join(str(c) for c in output.call_args_list))
        fake.close.assert_awaited_once()

    def test_redaction_retains_type_not_third_party_message(self):
        with patch('builtins.print') as output:
            probe.report_error(RuntimeError('SYNTHETIC_SECRET'), 'STARTUP')
        text = str(output.call_args)
        self.assertIn('UNEXPECTED_RuntimeError', text)
        self.assertNotIn('SYNTHETIC_SECRET', text)
