import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from voice_live.group_policy import GroupPolicyError, GroupScope, GroupSessionPolicy
from voice_live.group_runtime import (GroupCoordinator, CheckedBridge, GroupTurn,
    NOTICE_REVISION, run_approved_group, restricted_channel, discord_snapshot)
from test_group_policy import A, B, C, BOT, G, CH


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.roster = (A, B)
        self.stops = 0
        self.scope = GroupScope(G, CH, BOT, frozenset((A, B)), NOTICE_REVISION)
        self.p = GroupSessionPolicy(self.scope)
        self.c = GroupCoordinator(self.p, lambda: dict(guild=G, channel=CH,
            humans=self.roster), self.onstop)
        self.c.refresh()

    def onstop(self):
        self.stops += 1

    def agree_all(self):
        for actor in (A, B):
            self.c.agree(actor=actor, guild=G, channel=CH, audience=(A, B),
                revision=NOTICE_REVISION, adult=True, processing=True)
        self.c.start()

    def test_two_sequential_speakers_epochs(self):
        self.agree_all()
        for actor, epoch in ((A, 1), (B, 2)):
            for kind in ('speech_start', 'audio', 'speech_end'):
                event = self.c.input(dict(type=kind, epoch=epoch, speaker=actor))
                self.assertNotIn('speaker', event)  # Discord ID never goes to AI.
        self.assertEqual(self.stops, 0)

    def test_epoch_cannot_switch_actor(self):
        self.agree_all()
        self.c.input(dict(type='speech_start', epoch=1, speaker=A))
        with self.assertRaises(GroupPolicyError):
            self.c.input(dict(type='audio', epoch=1, speaker=B))
        self.assertEqual(self.stops, 1)

    def test_overlap_stops(self):
        self.agree_all()
        self.c.input(dict(type='speech_start', epoch=1, speaker=A))
        with self.assertRaises(GroupPolicyError):
            self.c.input(dict(type='speech_start', epoch=2, speaker=B))
        self.assertEqual(self.stops, 1)

    async def test_queued_audio_not_uploaded_after_unknown_join(self):
        self.agree_all()
        bridge = NS(audio=asyncio.Queue(), write=AsyncMock())
        checked = CheckedBridge(bridge, self.c)
        await bridge.audio.put(dict(type='speech_start', epoch=1, speaker=A))
        self.roster = (A, B, C)
        with self.assertRaises(GroupPolicyError):
            await checked.audio.get()
        bridge.write.assert_not_awaited()

    async def test_output_blocked_after_decline(self):
        self.agree_all()
        bridge = NS(audio=asyncio.Queue(), write=AsyncMock())
        checked = CheckedBridge(bridge, self.c)
        self.c.stop()
        with self.assertRaises(GroupPolicyError):
            await checked.write(dict(type='output', epoch=1, data='AAAA'))
        bridge.write.assert_not_awaited()

    async def test_output_blocked_on_member_leave(self):
        self.agree_all()
        bridge = NS(audio=asyncio.Queue(), write=AsyncMock())
        checked = CheckedBridge(bridge, self.c)
        self.roster = (A,)
        with self.assertRaises(GroupPolicyError):
            await checked.write(dict(type='output_end', epoch=1))
        bridge.write.assert_not_awaited()

    async def test_stop_command_allowed_after_revoke(self):
        self.agree_all()
        bridge = NS(audio=asyncio.Queue(), write=AsyncMock())
        checked = CheckedBridge(bridge, self.c)
        self.c.stop()
        await checked.write(dict(type='stop'))
        bridge.write.assert_awaited_once()

    def test_malformed_or_replayed_epoch_stops(self):
        for epoch in (0, 2, True, '1', None):
            with self.subTest(epoch=epoch):
                self.setUp()
                self.agree_all()
                with self.assertRaises(GroupPolicyError):
                    self.c.input(dict(type='speech_start', epoch=epoch, speaker=A))
                self.assertEqual(self.stops, 1)

    def test_snapshot_failure_stops_once(self):
        def bad():
            raise RuntimeError('fake snapshot unavailable')
        self.c.snapshot = bad
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                self.c.refresh()
        self.assertEqual(self.stops, 1)

    def test_non_sensitive_prompt_same_model_voice_no_transcription(self):
        turn = GroupTurn('FAKE', lambda: True)
        setup = turn.setup()['setup']
        self.assertEqual(setup['model'], 'models/gemini-3.1-flash-live-preview')
        self.assertEqual(setup['generationConfig']['speechConfig']['voiceConfig']['prebuiltVoiceConfig']['voiceName'], 'Sulafat')
        self.assertNotIn('inputAudioTranscription', setup)
        self.assertNotIn('outputAudioTranscription', setup)
        self.assertNotIn('maxOutputTokens', setup['generationConfig'])
        self.assertIn('Do not claim to recognize', setup['systemInstruction']['parts'][0]['text'])

    async def test_lifecycle_default_denies_before_any_connect(self):
        client = NS(user=NS(id=int(BOT)))
        channel = NS(connect=AsyncMock())
        with self.assertRaisesRegex(GroupPolicyError, 'APPROVAL_REQUIRED'):
            await run_approved_group(client, channel, self.p, 'FAKE', consent_ready=AsyncMock(), stopped=asyncio.Event())
        channel.connect.assert_not_awaited()

    def channel(self, *, denied=True, extra=False):
        everyone = NS(id=0)
        outsider = type('Target', (), {'id': int(C)})()
        ch = NS(id=int(CH), guild=NS(id=int(G), default_role=everyone),
            overwrites_for=lambda _: NS(connect=False if denied else None, view_channel=False),
            overwrites={outsider: NS(connect=True, view_channel=None)} if extra else {},
            voice_states={int(A): None, int(B): None, int(BOT): None})
        ch.guild.get_member = lambda actor: NS(bot=False)
        return ch

    def test_channel_must_deny_default_and_no_outsider_overwrite(self):
        for ch in (self.channel(denied=False), self.channel(extra=True)):
            with self.assertRaises(GroupPolicyError):
                restricted_channel(ch, self.scope)
        restricted_channel(self.channel(), self.scope)

    def test_unresolved_members_fail_closed(self):
        ch = self.channel()
        ch.guild.get_member = lambda actor: None
        with self.assertRaisesRegex(GroupPolicyError, 'UNRESOLVED'):
            discord_snapshot(ch, self.scope)

    async def test_lifecycle_missing_member_consent_never_connects(self):
        client = NS(user=NS(id=int(BOT)))
        channel = self.channel()
        channel.connect = AsyncMock()
        async def partial(c):
            c.agree(actor=A, guild=G, channel=CH, audience=(A, B),
                revision=NOTICE_REVISION, adult=True, processing=True)
        with self.assertRaisesRegex(GroupPolicyError, 'MISSING'):
            await run_approved_group(client, channel, self.p, 'FAKE', consent_ready=partial,
                stopped=asyncio.Event(), notice_approved=True, confirmed_free_tier=True)
        channel.connect.assert_not_awaited()

    async def test_lifecycle_consent_audience_race_never_connects(self):
        client = NS(user=NS(id=int(BOT)))
        channel = self.channel()
        channel.connect = AsyncMock()
        async def consent(c):
            for actor in (A, B):
                c.agree(actor=actor, guild=G, channel=CH, audience=(A, B),
                    revision=NOTICE_REVISION, adult=True, processing=True)
            channel.voice_states[int(C)] = None
        with self.assertRaisesRegex(GroupPolicyError, 'UNSAFE'):
            await run_approved_group(client, channel, self.p, 'FAKE', consent_ready=consent,
                stopped=asyncio.Event(), notice_approved=True, confirmed_free_tier=True)
        channel.connect.assert_not_awaited()

    async def test_lifecycle_no_free_assertion_never_connects(self):
        client = NS(user=NS(id=int(BOT)))
        channel = self.channel()
        channel.connect = AsyncMock()
        with self.assertRaisesRegex(Exception, 'FREE_TIER_NOT_CONFIRMED'):
            await run_approved_group(client, channel, self.p, 'FAKE', consent_ready=AsyncMock(),
                stopped=asyncio.Event(), notice_approved=True)
        channel.connect.assert_not_awaited()

    async def test_notice_handler_default_no_send(self):
        from voice_live.group_runtime import show_approved_consent
        interaction = NS(response=NS(send_message=AsyncMock()))
        with self.assertRaises(GroupPolicyError):
            await show_approved_consent(interaction, self.c)
        interaction.response.send_message.assert_not_awaited()

    async def test_notice_handler_ephemeral_no_mentions_authenticated_actor(self):
        from voice_live.group_runtime import show_approved_consent
        interaction = NS(user=NS(id=int(A), voice=NS(channel=NS(id=int(CH)))),
            guild_id=int(G), response=NS(send_message=AsyncMock()))
        view = await show_approved_consent(interaction, self.c, notice_approved=True)
        call = interaction.response.send_message.call_args
        self.assertTrue(call.kwargs['ephemeral'])
        self.assertEqual(call.kwargs['allowed_mentions'].to_dict(), {'parse': []})
        self.assertIn(A, call.args[0])
        self.assertIn(B, call.args[0])
        wrong = NS(user=NS(id=int(B)), guild_id=int(G))
        self.assertFalse(await view.interaction_check(wrong))
        self.assertTrue(await view.interaction_check(interaction))
        view.stop()
