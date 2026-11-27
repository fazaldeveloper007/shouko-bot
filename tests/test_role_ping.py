"""Direct role/member ping requests: handled deterministically in code.

An explicit grounded request must not depend on the model emitting a
[[tag:]] marker: the ping itself is executed by code and the reported
result follows what the send actually did. Failure paths say so instead of
letting unsupported success prose stand."""
import types
import unittest
from unittest.mock import AsyncMock

from tests.test_offline_harness import HarnessBase, OWNER, OTHER


def role(rid, name, mentionable=True):
    return types.SimpleNamespace(id=rid, name=name, mentionable=mentionable,
                                 is_default=lambda: False)


class DirectTag(HarnessBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.silly = role(777, 'Silly role', mentionable=False)
        self.guild.roles = [self.silly]

    def sent_texts(self):
        return [str(c.args[0]) for c in self.channel.send.await_args_list]

    def reply_texts(self, m):
        return [str(c.args[0]) for c in m.reply.await_args_list]

    async def test_direct_role_ping_executes_without_marker(self):
        # bare request; even if the model would only talk, code executes
        self.script('ping kar diya!')  # brain output must be irrelevant here
        m = self.msg(self.owner, 'Kristy Silly role ping karo')
        await self.bot.on_message(m)
        texts = self.sent_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn('<@&777>', texts[0])
        call = self.channel.send.await_args
        self.assertIn(self.silly, call.kwargs['allowed_mentions'].roles)

    async def test_screenshot_wording_inline_backticks(self):
        self.script('lo ji, silly role wale sabhi ko ping kar diya hai!')
        m = self.msg(self.owner, 'Kristy `Silly role` Naam ka ek role hai usko ping karo')
        await self.bot.on_message(m)
        texts = self.sent_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn('<@&777>', texts[0])

    async def test_wrong_marker_does_not_change_target(self):
        # model claims a different target; the deterministic handler resolves
        # only from the user's own current request
        self.script('[[tag:UG]] ko ping kar diya!')
        m = self.msg(self.owner, 'Kristy Silly role ping karo')
        await self.bot.on_message(m)
        texts = self.sent_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn('<@&777>', texts[0])
        self.assertNotIn(f'<@{OTHER}>', texts[0])

    async def test_member_ping_executes(self):
        m = self.msg(self.owner, 'Kristy UG ko ping karo')
        await self.bot.on_message(m)
        texts = self.sent_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn(f'<@{OTHER}>', texts[0])
        call = self.channel.send.await_args
        self.assertFalse(call.kwargs['allowed_mentions'].roles)

    async def test_unknown_name_asks_no_send(self):
        m = self.msg(self.owner, 'Kristy Ghost role ping karo')
        await self.bot.on_message(m)
        self.assertEqual(self.sent_texts(), [])
        self.assertIn('nahi mila', ' '.join(self.reply_texts(m)))

    async def test_ambiguous_role_asks_no_send(self):
        dupe = role(778, 'Silly role')
        self.guild.roles = [self.silly, dupe]
        m = self.msg(self.owner, 'Kristy Silly role ping karo')
        await self.bot.on_message(m)
        self.assertEqual(self.sent_texts(), [])
        self.assertIn('nahi mila', ' '.join(self.reply_texts(m)))

    async def test_unpingable_role_refused_no_send(self):
        self.silly.mentionable = False
        self.channel.permissions_for = lambda who: types.SimpleNamespace(mention_everyone=False)
        m = self.msg(self.owner, 'Kristy Silly role ping karo')
        await self.bot.on_message(m)
        self.assertEqual(self.sent_texts(), [])
        text = ' '.join(self.reply_texts(m))
        self.assertIn('ping nahi kar paungi', text)
        self.assertIn('Allow anyone to @mention this role', text)

    async def test_everyone_refused(self):
        m = self.msg(self.owner, 'Kristy sabko ping karo')
        await self.bot.on_message(m)
        self.assertEqual(self.sent_texts(), [])
        self.assertIn('@everyone wala ping nahi karti', ' '.join(self.reply_texts(m)))

    async def test_bare_ping_asks_who(self):
        m = self.msg(self.owner, 'Kristy ping karo')
        await self.bot.on_message(m)
        self.assertEqual(self.sent_texts(), [])
        self.assertIn('Kisko ping karna hai', ' '.join(self.reply_texts(m)))

    async def test_send_error_reported_never_retried(self):
        self.channel.send = AsyncMock(side_effect=RuntimeError('gateway'))
        m = self.msg(self.owner, 'Kristy Silly role ping karo')
        await self.bot.on_message(m)
        self.assertEqual(self.channel.send.await_count, 1)  # no auto-retry
        self.assertIn('result confirm nahi hua', ' '.join(self.reply_texts(m)))

    async def test_ordinary_chat_ping_word_not_hijacked(self):
        self.script('haha nice ping 😄')
        m = self.msg(self.owner, 'Kristy uska ping kitna tha match mein?')
        await self.bot.on_message(m)
        self.assertEqual(self.sent_texts(), [])


if __name__ == '__main__':
    unittest.main()
