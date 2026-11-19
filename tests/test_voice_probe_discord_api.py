"""Selection/start/privacy gates using real discord.py API objects, no network."""
import types
import unittest
from unittest.mock import AsyncMock, patch
import test_voice_probe as original
probe = original.probe


def real_guild():
    guild = object.__new__(probe.discord.Guild)
    guild.id = 100
    guild._channels = {}
    guild._voice_states = {}
    for cid in (10, 11):
        channel = object.__new__(probe.discord.VoiceChannel)
        channel.id = cid
        channel.guild = guild
        channel.position = cid
        guild._channels[cid] = channel
    guild._voice_states[42] = types.SimpleNamespace(channel=guild._channels[10])
    return guild


class RealAPI(unittest.IsolatedAsyncioTestCase):
    def test_real_channel_voice_states_without_member_cache(self):
        guild = real_guild()
        self.assertFalse(hasattr(guild, 'voice_states'))  # regression trigger
        channel = guild.voice_channels[0]
        self.assertTrue(probe.safe_members(channel, 42, 99))
        guild._voice_states[77] = types.SimpleNamespace(channel=channel)
        self.assertFalse(probe.safe_members(channel, 42, 99))
        guild._voice_states.pop(77)
        guild._voice_states[99] = types.SimpleNamespace(channel=channel)
        self.assertTrue(probe.safe_members(channel, 42, 99))
        guild._voice_states.pop(42)
        self.assertFalse(probe.safe_members(channel, 42, 99))

    async def exercise(self, answer='CANCEL', bot_elsewhere=False):
        guild = real_guild()
        if bot_elsewhere:
            guild._voice_states[99] = types.SimpleNamespace(channel=guild._channels[11])
        callbacks = {}
        def register(fn):
            callbacks[fn.__name__] = fn
            return fn
        async def start(*args, **kwargs):
            await callbacks['on_ready']()
        client = types.SimpleNamespace(event=register, start=start, close=AsyncMock(),
                                       user=types.SimpleNamespace(id=99), guilds=[guild])
        process = types.SimpleNamespace(wait=AsyncMock(return_value=0))
        with patch.object(probe.sys.stdin, 'isatty', return_value=True), \
             patch.object(probe.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)), \
             patch.object(probe, 'existing_token', return_value='OFFLINE_DUMMY'), \
             patch.object(probe.discord, 'Client', return_value=client), \
             patch.object(probe, 'console_line', AsyncMock(return_value=answer)) as console, \
             patch.object(probe, 'ProbeBridge') as bridge, patch('builtins.print') as output:
            await probe.run(42)
            bridge.assert_not_called()
            text = ' '.join(str(call) for call in output.call_args_list)
            self.assertNotIn('AttributeError', text)
            self.assertIn('PROBE_JOIN_NOT_REQUESTED', text)
            if bot_elsewhere:
                console.assert_not_awaited()
                self.assertIn('already in a VC', text)
            else:
                console.assert_awaited_once()
                self.assertIn('Requester 42; guild 100; channel 10', text)
                self.assertIn('Type START', text)
        client.close.assert_awaited_once()

    async def test_actual_api_reaches_start_gate_then_cancel_without_join(self):
        await self.exercise()

    async def test_actual_api_blocks_bot_already_in_other_vc(self):
        await self.exercise(bot_elsewhere=True)
