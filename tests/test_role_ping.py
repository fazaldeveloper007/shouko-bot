"""Direct role ping requests in chat: the [[tag:role]] marker must become a
real, allowed role mention - or an honest report when the role cannot
actually notify. Success prose without a real ping is the bug being fixed."""
import types
import unittest

from tests.test_offline_harness import HarnessBase, OWNER, OTHER


def role(rid, name, mentionable=True):
    return types.SimpleNamespace(id=rid, name=name, mentionable=mentionable,
                                 is_default=lambda: False)


class RolePing(HarnessBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.silly = role(777, 'Silly role', mentionable=True)
        self.guild.roles = [self.silly]

    def last_call(self, m):
        self.assertTrue(m.reply.await_args_list)
        return m.reply.await_args_list[-1]

    async def test_role_ping_marker_becomes_real_allowed_mention(self):
        self.script('lo ji, [[tag:Silly role]] wale sabhi ko ping kar diya hai!')
        m = self.msg(self.owner, 'Kristy Silly role ping karo')
        await self.bot.on_message(m)
        call = self.last_call(m)
        self.assertIn('<@&777>', call.args[0])
        self.assertIn(self.silly, call.kwargs['allowed_mentions'].roles)

    async def test_unpingable_role_is_reported_not_faked(self):
        # role not mentionable and Kristy lacks Mention Roles here: the reply
        # must say the ping did not happen, never claim it did.
        self.silly.mentionable = False
        self.channel.permissions_for = lambda who: types.SimpleNamespace(mention_everyone=False)
        self.script('[[tag:Silly role]] ping kar diya!')
        m = self.msg(self.owner, 'Kristy Silly role ping karo')
        await self.bot.on_message(m)
        call = self.last_call(m)
        text = call.args[0]
        self.assertNotIn('<@&777>', text)
        self.assertIn('@Silly role', text)
        self.assertTrue('ping nahi' in text or 'ping maine nahi' in text, text)
        self.assertFalse(call.kwargs['allowed_mentions'].roles)

    async def test_member_tag_still_resolves(self):
        self.script('[[tag:UG]] ko bula diya!')
        m = self.msg(self.owner, 'Kristy UG ko tag karo')
        await self.bot.on_message(m)
        call = self.last_call(m)
        self.assertIn(f'<@{OTHER}>', call.args[0])
        self.assertFalse(call.kwargs['allowed_mentions'].roles)

    async def test_unknown_name_asks_instead_of_guessing(self):
        self.script('[[tag:Ghost role]] ping kar diya!')
        m = self.msg(self.owner, 'Kristy Ghost role ping karo')
        await self.bot.on_message(m)
        call = self.last_call(m)
        text = call.args[0]
        self.assertNotIn('<@&', text)
        self.assertIn('@Ghost role', text)

    async def test_marker_stripped_for_non_action_text(self):
        # Backticked/quoted requests are deliberately not action-eligible:
        # no ping may happen, and the marker must not leak through.
        self.script('[[tag:Silly role]] ping kar diya!')
        m = self.msg(self.owner, 'Kristy `Silly role` ping karo')
        await self.bot.on_message(m)
        call = self.last_call(m)
        self.assertNotIn('<@&777>', call.args[0])


if __name__ == '__main__':
    unittest.main()
