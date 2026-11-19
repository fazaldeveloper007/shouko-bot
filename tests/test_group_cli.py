import asyncio
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from test_group_policy import A, B, BOT, G, CH
from voice_live.group_config import load_config, configured_scope
from voice_live.group_policy import GroupPolicyError
from voice_live import group


class GroupCliTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return dict(schema=1, guild_id=G, owner_id=A, voice_channel_id=CH,
            notice_revision='group-free-draft1', notice_approved=False,
            maximum_concurrent_humans=8, members=[dict(user_id=B, label='inert-label')],
            source_message_id='synthetic', consents=[])

    def load(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'private.json'
            path.write_text(json.dumps(data))
            return load_config(path)

    def test_private_config_owner_plus_members_no_consent(self):
        data, ids=self.load(self.config())
        scope=configured_scope(data,ids,BOT)
        self.assertEqual(scope.allowlist,frozenset((A,B)))
        self.assertEqual(data['consents'],[])

    def test_missing_channel_cannot_be_inferred(self):
        data=self.config();data['voice_channel_id']=None
        data, ids=self.load(data)
        with self.assertRaises(GroupPolicyError):
            configured_scope(data,ids,BOT)

    def test_config_cannot_store_reusable_consent_or_notice_approval(self):
        for name,value in [('consents',[A]),('notice_approved',True),('maximum_concurrent_humans',12)]:
            data=self.config();data[name]=value
            with self.assertRaises(GroupPolicyError):
                self.load(data)

    async def test_cli_defaults_block_before_credentials_and_network(self):
        with patch.object(group,'existing_token') as token,patch.object(group.discord,'Client') as client:
            with self.assertRaises(Exception):
                await group.run('/nonexistent')
            token.assert_not_called();client.assert_not_called()
            with self.assertRaises(GroupPolicyError):
                await group.run('/nonexistent',confirmed_free_tier=True)
            token.assert_not_called();client.assert_not_called()

    async def test_cli_missing_channel_blocks_before_credentials(self):
        data=self.config();data['voice_channel_id']=None
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'private.json';path.write_text(json.dumps(data))
            with patch.object(group,'existing_token') as token,patch.object(group.discord,'Client') as client:
                with self.assertRaises(GroupPolicyError):
                    await group.run(path,confirmed_free_tier=True,notice_approved=True)
                token.assert_not_called();client.assert_not_called()

    async def test_shared_view_authenticated_actor_and_buttons(self):
        from types import SimpleNamespace as NS
        c=NS(policy=NS(_roster=frozenset((A,B)),scope=NS(guild=G)))
        view=group.SharedConsentView(c,asyncio.Event())
        self.assertEqual([b.label for b in view.children],['I am 18+ and Agree','Decline / Stop'])
        self.assertTrue(await view.interaction_check(NS(user=NS(id=int(A)),guild_id=int(G))))
        self.assertFalse(await view.interaction_check(NS(user=NS(id=int(BOT)),guild_id=int(G))))
        view.stop()
