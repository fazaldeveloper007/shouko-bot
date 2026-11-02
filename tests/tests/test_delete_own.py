import unittest
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import delete_own

BOT=99
def build(target_author=BOT,requester=1,owner='',ref_author=1,perm=False,channel_id=20,target_channel=20):
    chan=S(id=channel_id,permissions_for=lambda m:S(manage_messages=perm))
    replied=S(author=S(id=ref_author))
    target=S(id=500,author=S(id=target_author),channel=S(id=target_channel),reference=S(resolved=replied),delete=AsyncMock())
    msg=S(author=S(id=requester),guild=S(id=1),channel=chan,reference=S(message_id=500,resolved=target),message_snapshots=None)
    return delete_own.Deleter(owner,lambda:BOT),msg,target
ok=lambda *a,**k:'ok'

class T(unittest.IsolatedAsyncioTestCase):
    def test_phrases(self):
        for t in ('delete','ye message delete kardo','Kristy delete this message','isko hata do','delete kar do','message hatao','ok delete'):
            self.assertTrue(delete_own.is_request(t),t)
        for t in ('delete all messages','delete 5 messages','kya delete karu','ye message delete mat karna','hello','delete channel','delete kardo #general'):
            self.assertFalse(delete_own.is_request(t),t)
    async def test_deletes_own_message_for_member_it_replied_to(self):
        d,m,t=build();self.assertTrue(await d.handle(m,'ye message delete kardo',ok));t.delete.assert_awaited_once()
    async def test_owner_and_manager_allowed_stranger_not(self):
        d,m,t=build(requester=7,ref_author=1,owner='7');await d.handle(m,'delete',ok);t.delete.assert_awaited_once()
        d,m,t=build(requester=8,perm=True);await d.handle(m,'delete',ok);t.delete.assert_awaited_once()
        d,m,t=build(requester=8);self.assertFalse(await d.handle(m,'delete',ok));t.delete.assert_not_awaited()
    async def test_never_deletes_another_members_message(self):
        d,m,t=build(target_author=5,owner='1');self.assertFalse(await d.handle(m,'delete',ok));t.delete.assert_not_awaited()
    async def test_other_channel_or_no_reference_or_guard_refusal(self):
        d,m,t=build(target_channel=21);self.assertFalse(await d.handle(m,'delete',ok));t.delete.assert_not_awaited()
        d,m,t=build();m.reference=None;self.assertFalse(await d.handle(m,'delete',ok))
        d,m,t=build();self.assertFalse(await d.handle(m,'delete',lambda *a,**k:None));t.delete.assert_not_awaited()
    async def test_delete_failure_is_silent(self):
        d,m,t=build();t.delete.side_effect=RuntimeError();self.assertTrue(await d.handle(m,'delete',ok))
