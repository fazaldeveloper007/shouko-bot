import unittest
from voice_live.group_policy import GroupPolicyError, GroupScope, GroupSessionPolicy

A, B, C, BOT = ('1'*18, '2'*18, '3'*18, '4'*18)
G, CH = '5'*18, '6'*18


class GroupPolicyTests(unittest.TestCase):
    def setUp(self):
        self.p = GroupSessionPolicy(GroupScope(G, CH, BOT, frozenset((A, B)), 'draft1'))

    def roster(self, people=(A, B), **kw):
        return self.p.observe_roster(guild=G, channel=CH, humans=people, **kw)

    def agree(self, actor, **kw):
        args = dict(actor=actor, guild=G, channel=CH, audience=(A, B), notice_revision='draft1', adult=True, processing=True)
        args.update(kw)
        self.p.agree(**args)

    def active(self):
        self.roster()
        self.agree(A)
        self.agree(B)
        self.p.start()

    def test_no_audio_before_all_agree(self):
        self.roster()
        self.agree(A)
        with self.assertRaisesRegex(GroupPolicyError, 'MISSING'):
            self.p.start()
        with self.assertRaisesRegex(GroupPolicyError, 'NOT_ALLOWED'):
            self.p.require_audio(speaker=A)

    def test_all_agree_audio_and_playback(self):
        self.active()
        for actor in (A, B):
            self.p.require_audio(speaker=actor)
        self.p.require_playback()

    def test_nonmember_actor_rejected(self):
        self.roster()
        with self.assertRaisesRegex(GroupPolicyError, 'INVALID'):
            self.agree(C)

    def test_no_truthy_nonboolean_agreement(self):
        self.roster()
        for field in ('adult', 'processing'):
            for value in (False, 1, 'yes', None):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(GroupPolicyError):
                        self.agree(A, **{field: value})

    def test_wrong_context_or_stale_notice_rejected(self):
        self.roster()
        for kw in ({'guild': C}, {'channel': C}, {'audience': (A,)}, {'notice_revision': 'old'}):
            with self.subTest(kw=kw):
                with self.assertRaises(GroupPolicyError):
                    self.agree(A, **kw)

    def test_roster_change_before_start_clears_agreements(self):
        self.roster()
        self.agree(A)
        self.roster((A,))
        with self.assertRaisesRegex(GroupPolicyError, 'MISSING'):
            self.p.start()
        self.agree(A, audience=(A,))
        self.p.start()
        self.p.require_audio(speaker=A)

    def test_unknown_join_stops_all(self):
        self.active()
        with self.assertRaisesRegex(GroupPolicyError, 'UNSAFE'):
            self.roster((A, B, C))
        with self.assertRaisesRegex(GroupPolicyError, 'CLOSED'):
            self.p.require_playback()

    def test_approved_member_leave_survives_once_adapter_rederives(self):
        self.active()
        self.roster((A,))  # a leave inside the allowed audience no longer stops
        self.p._agreed = {A}  # what the coordinator re-derives from grants
        self.p.require_audio(speaker=A)
        self.p.require_playback()

    def test_audio_without_adapter_rederive_fails_closed(self):
        self.active()
        self.roster((A,))
        # If the adapter ever skips re-derivation, the stale mismatch still
        # fails closed rather than drifting.
        with self.assertRaisesRegex(GroupPolicyError, 'NOT_ALLOWED'):
            self.p.require_audio(speaker=A)

    def test_allowlisted_joiner_does_not_stop_session(self):
        p = GroupSessionPolicy(GroupScope(G, CH, BOT, frozenset((A, B, C)), 'draft1'))
        p.observe_roster(guild=G, channel=CH, humans=(A, B))
        for actor in (A, B):
            p.agree(actor=actor, guild=G, channel=CH, audience=(A, B),
                    notice_revision='draft1', adult=True, processing=True)
        p.start()
        p.observe_roster(guild=G, channel=CH, humans=(A, B, C))  # allowed, ungranted
        # Their audio is not allowed until the adapter records their agreement.
        with self.assertRaisesRegex(GroupPolicyError, 'NOT_ALLOWED'):
            p.require_audio(speaker=C)

    def test_empty_channel_stops(self):
        self.active()
        with self.assertRaisesRegex(GroupPolicyError, 'NO_LISTENERS'):
            self.roster(())

    def test_other_bot_not_silently_allowed(self):
        with self.assertRaisesRegex(GroupPolicyError, 'UNSAFE'):
            self.roster(other_bots=(C,))

    def test_decline_revokes_audio_playback(self):
        self.active()
        self.p.decline(actor=B)
        for action in (lambda: self.p.require_audio(speaker=A), self.p.require_playback, self.p.start):
            with self.assertRaises(GroupPolicyError):
                action()

    def test_bad_speaker_stops_session(self):
        self.active()
        with self.assertRaises(GroupPolicyError):
            self.p.require_audio(speaker=C)
        with self.assertRaises(GroupPolicyError):
            self.p.require_playback()

    def test_config_rejects_duplicate_and_bot_members(self):
        for values in ((A, A), (BOT,), (), ('x',)):
            with self.subTest(values=values):
                with self.assertRaises(GroupPolicyError):
                    GroupScope(G, CH, BOT, values, 'draft1')

    def test_channel_gate_value_validated(self):
        with self.assertRaisesRegex(GroupPolicyError, 'GATE'):
            GroupScope(G, CH, BOT, (A, B), 'draft1', 'public')
        for gate in ('restricted', 'open'):
            self.assertEqual(GroupScope(G, CH, BOT, (A, B), 'draft1', gate).channel_gate, gate)
        self.assertEqual(GroupScope(G, CH, BOT, (A, B), 'draft1').channel_gate, 'restricted')

    def test_malformed_roster_stops(self):
        self.active()
        with self.assertRaises(GroupPolicyError):
            self.roster((A, A))
        with self.assertRaises(GroupPolicyError):
            self.p.require_playback()

    def test_same_roster_does_not_erase_consent(self):
        self.active()
        self.roster((B, A))
        self.p.require_audio(speaker=B)

    def test_no_disk_network_persona_or_diary_imports(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path('voice_live/group_policy.py').read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            if isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertEqual(imports, {'dataclasses', 're'})
