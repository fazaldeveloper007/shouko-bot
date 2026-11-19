import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from voice_live.group_consent import ConsentStore, scope_key
from voice_live.group_policy import GroupScope, GroupSessionPolicy, GroupPolicyError
from voice_live.group_runtime import GroupCoordinator, NOTICE_REVISION
from test_group_policy import A,B,C,BOT,G,CH


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'private'/'consent.json'
        self.store=ConsentStore(self.path)
        self.scope=GroupScope(G,CH,BOT,frozenset((A,B)),NOTICE_REVISION)
        self.stops=0
        self.roster=(A,B)

    def tearDown(self):self.temp.cleanup()

    def coordinator(self):
        def stop():self.stops+=1
        return GroupCoordinator(GroupSessionPolicy(self.scope),lambda:dict(guild=G,channel=CH,humans=self.roster),stop,self.store)

    def grant(self, actor):
        self.store.agree(self.scope,actor,adult=True,processing=True,future=True)

    def test_missing_no_grants(self):
        self.assertEqual(self.store.valid(self.scope),frozenset())

    def test_future_explicit_not_inferred(self):
        for field in ('future','adult','processing'):
            for value in (False,1,'yes',None):
                args=dict(future=True,adult=True,processing=True);args[field]=value
                with self.assertRaises(GroupPolicyError):self.store.agree(self.scope,A,**args)
        self.assertEqual(self.store.valid(self.scope),frozenset())

    def test_restart_same_fixed_group_roster_subset_no_reprompt(self):
        self.grant(A);self.grant(B)
        for roster in ((A,B),(A,),(B,)):
            self.roster=roster
            c=self.coordinator();c.refresh();c.start();c.playback();c.stop()
        self.assertEqual(self.store.valid(self.scope),frozenset((A,B)))

    def test_channel_alone_not_new_allowed_audience(self):
        self.grant(A)
        other=GroupScope(G,C,BOT,self.scope.allowlist,NOTICE_REVISION)
        self.assertEqual(scope_key(other),scope_key(self.scope))
        self.assertIn(A,self.store.valid(other))

    def test_allowlist_change_invalidates_and_reverting_does_not_resurrect(self):
        self.grant(A);self.grant(B)
        changed=GroupScope(G,CH,BOT,frozenset((A,B,C)),NOTICE_REVISION)
        self.assertFalse(self.store.valid(changed))
        self.assertFalse(self.store.valid(self.scope))

    def test_revision_guild_bot_change_invalidates(self):
        for scope in (GroupScope(C,CH,BOT,self.scope.allowlist,NOTICE_REVISION),GroupScope(G,CH,C,self.scope.allowlist,NOTICE_REVISION),GroupScope(G,CH,BOT,self.scope.allowlist,'new-data-v3')):
            self.grant(A)
            self.assertFalse(self.store.valid(scope))

    def test_revoke_active_stops_and_restart_missing_member_grant(self):
        self.grant(A);self.grant(B)
        c=self.coordinator();c.refresh();c.start()
        self.store.revoke(B)
        with self.assertRaisesRegex(GroupPolicyError,'REVOKED'):c.playback()
        fresh=self.coordinator();fresh.refresh()
        with self.assertRaisesRegex(GroupPolicyError,'MISSING'):fresh.start()
        self.assertIn(A,self.store.valid(self.scope))
        self.assertNotIn(B,self.store.valid(self.scope))

    def test_new_unconsented_member_blocks_before_any_start(self):
        self.grant(A)
        c=self.coordinator();c.refresh()
        with self.assertRaisesRegex(GroupPolicyError,'MISSING'):c.start()

    def test_authenticated_adapter_agree_saves_only_actor(self):
        c=self.coordinator();c.refresh()
        c.agree(actor=A,guild=G,channel=CH,audience=(A,B),revision=NOTICE_REVISION,adult=True,processing=True,future=True)
        self.assertEqual(self.store.valid(self.scope),frozenset((A,)))
        with self.assertRaises(GroupPolicyError):
            c.agree(actor=C,guild=G,channel=CH,audience=(A,B),revision=NOTICE_REVISION,adult=True,processing=True,future=True)

    def test_save_failure_never_leaves_usable_inmemory_grant(self):
        from unittest.mock import patch
        c=self.coordinator();c.refresh()
        with patch.object(self.store,'_write',side_effect=GroupPolicyError('GROUP_CONSENT_SAVE_FAILED')):
            with self.assertRaises(GroupPolicyError):
                c.agree(actor=A,guild=G,channel=CH,audience=(A,B),revision=NOTICE_REVISION,adult=True,processing=True,future=True)
        with self.assertRaises(GroupPolicyError):c.playback()

    def test_corrupt_store_fails_closed_never_treated_as_agreed(self):
        self.grant(A)
        self.path.write_text('{broken')
        with self.assertRaises(GroupPolicyError):self.store.valid(self.scope)
        c=self.coordinator()
        with self.assertRaises(GroupPolicyError):c.refresh()
        self.assertTrue(c.stopped)

    def test_concurrent_grants_not_lost_and_private_permissions(self):
        self.store.valid(self.scope)
        with ThreadPoolExecutor(2) as pool:list(pool.map(self.grant,(A,B)))
        self.assertEqual(self.store.valid(self.scope),frozenset((A,B)))
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        self.assertEqual(Path(str(self.path)+'.lock').stat().st_mode&0o777,0o600)
        self.assertEqual(self.path.parent.stat().st_mode&0o777,0o700)

    def test_store_contains_only_ids_scope_and_boolean_grant_not_content(self):
        self.grant(A)
        value=json.loads(self.path.read_text())
        self.assertEqual(set(value),{'schema','current_scope','grants'})
        self.assertEqual(set(value['grants'][A]),{'scope','future','adult','processing'})

    def test_active_roster_change_still_stops_but_next_subset_reuses(self):
        self.grant(A);self.grant(B)
        c=self.coordinator();c.refresh();c.start()
        self.roster=(A,)
        with self.assertRaises(GroupPolicyError):c.refresh()
        fresh=self.coordinator();fresh.refresh();fresh.start();fresh.playback()


class DurableViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_group_start_has_no_agree_button(self):
        from types import SimpleNamespace as NS
        from voice_live.group import SharedConsentView
        c=NS(policy=NS(_roster=frozenset((A,B)),_agreed={A,B},scope=NS(guild=G)))
        view=SharedConsentView(c,asyncio.Event())
        self.assertEqual([x.label for x in view.children],['Decline / Stop','Details','Revoke'])
        view.stop()

    async def test_real_actor_button_sets_future_grant_not_owner_assertion(self):
        from types import SimpleNamespace as NS
        from unittest.mock import AsyncMock
        from voice_live.group import SharedConsentView
        with tempfile.TemporaryDirectory() as directory:
            store=ConsentStore(Path(directory)/'consent.json')
            scope=GroupScope(G,CH,BOT,frozenset((A,B)),NOTICE_REVISION)
            c=GroupCoordinator(GroupSessionPolicy(scope),lambda:dict(guild=G,channel=CH,humans=(A,B)),lambda:None,store)
            c.refresh();view=SharedConsentView(c,asyncio.Event())
            interaction=NS(user=NS(id=int(A),voice=NS(channel=NS(id=int(CH)))),guild_id=int(G),response=NS(send_message=AsyncMock()))
            self.assertTrue(await view.interaction_check(interaction))
            await view.agree_button.callback(interaction)
            self.assertEqual(store.valid(scope),frozenset((A,)))
            self.assertIn('saved',interaction.response.send_message.call_args.args[0])
            view.stop()

    async def test_button_revoke_deletes_only_authenticated_member_and_stops(self):
        from types import SimpleNamespace as NS
        from unittest.mock import AsyncMock
        from voice_live.group import SharedConsentView
        with tempfile.TemporaryDirectory() as directory:
            store=ConsentStore(Path(directory)/'consent.json')
            scope=GroupScope(G,CH,BOT,frozenset((A,B)),NOTICE_REVISION)
            for actor in (A,B):store.agree(scope,actor,adult=True,processing=True,future=True)
            c=GroupCoordinator(GroupSessionPolicy(scope),lambda:dict(guild=G,channel=CH,humans=(A,B)),lambda:None,store)
            c.refresh();view=SharedConsentView(c,asyncio.Event())
            interaction=NS(user=NS(id=int(A)),guild_id=int(G),response=NS(send_message=AsyncMock()))
            self.assertTrue(await view.interaction_check(interaction))
            await view.revoke_button.callback(interaction)
            self.assertEqual(store.valid(scope),frozenset((B,)))
            self.assertTrue(c.stopped)
            view.stop()
