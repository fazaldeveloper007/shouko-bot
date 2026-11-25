"""Fake-runtime end-to-end: real bot.on_message -> scripted Gemini -> real reply
formatting -> actions dispatch -> server builder. No network, no Discord.
Whether the live model emits the plan marker reliably is NOT proven here."""
import json
import re
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

try:
    from test_offline_harness import HarnessBase, OWNER, OTHER, GUILD
    from test_server_builder import make_guild
except ImportError:  # run as tests.<module>
    from tests.test_offline_harness import HarnessBase, OWNER, OTHER, GUILD
    from tests.test_server_builder import make_guild


def plan(ops, summary='Gaming layout'):
    return '[[do:server plan]]\n' + json.dumps({'summary': summary, 'ops': ops}, indent=1)


class E2E(HarnessBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        g = make_guild(owner_id=OWNER)
        g.id, g.name = GUILD, 'Gaming Impact'
        g.members, g.get_member = self.guild.members, self.guild.get_member
        g.text_channels = self.guild.text_channels
        g.me = types.SimpleNamespace(
            id=99, top_role=g.me.top_role, roles=g.me.roles, guild_permissions=g.me.guild_permissions,
            display_name='Kristy')
        self.guild = g
        self.g = g
        import server_builder
        server_builder.OP_GAP = 0  # keep the test fast

    def code(self, text):
        return re.search(r'server confirm ([a-f0-9]{10})', text).group(1)

    async def ask(self, author, text, reply):
        self.script(reply)
        m = self.msg(author, text, guild=self.g)
        await self.bot.on_message(m)
        return m

    async def say(self, author, text):
        m = self.msg(author, text, guild=self.g)
        await self.bot.on_message(m)
        return m

    async def test_plan_preview_then_owner_confirm_applies_once(self):
        m = await self.ask(self.owner, 'Kristy server ke channels aur roles organise karo, gaming theme',
                           plan([{'op': 'create_category', 'name': 'Scrims'},
                                 {'op': 'create_channel', 'name': 'scrim-chat', 'type': 'text', 'category': 'Scrims'},
                                 {'op': 'rename', 'kind': 'channel', 'id': 51, 'name': 'main-chat'}]))
        system = self.last_payload()['system_instruction']['parts'][0]['text']
        self.assertIn('SERVER BUILDER', system)
        self.assertIn('SERVER LAYOUT (read-only data', system)       # owner-only layout block with ids
        self.assertIn('[category 50]', system)
        out = self.reply_text(m)
        self.assertIn('NAHI hua', out)
        self.assertIn('create category "Scrims"', out)
        self.assertNotIn('[[do:server', out)
        # preview changed nothing: default-on never auto-applies
        self.g.create_category.assert_not_called()
        self.g.create_text_channel.assert_not_called()
        self.g.chans[51].edit.assert_not_called()
        code = self.code(out)
        self.brain.reset_mock()
        m2 = await self.say(self.owner, f'Kristy server confirm {code}')
        self.brain.assert_not_called()                 # deterministic, zero API calls
        self.g.create_category.assert_awaited_once()
        self.g.create_text_channel.assert_awaited_once()
        self.g.chans[51].edit.assert_awaited_once()
        self.assertIn('3 steps done', self.reply_text(m2))
        m3 = await self.say(self.owner, f'Kristy server confirm {code}')   # single-use
        self.assertEqual(self.g.create_category.await_count, 1)
        self.assertIn('nahi mila', self.reply_text(m3))

    async def test_non_owner_cannot_plan_or_confirm(self):
        m = await self.ask(self.other, 'Kristy server ke channels badal do', plan([{'op': 'create_category', 'name': 'X'}]))
        self.assertIn('actual owner', self.reply_text(m))
        self.assertNotIn('SERVER LAYOUT (read-only data', self.last_payload()['system_instruction']['parts'][0]['text'])
        self.assertFalse(self.bot._actions.builder.pending)
        # owner makes a plan; another member tries to confirm it, also unaddressed
        mo = await self.ask(self.owner, 'Kristy server mein ek category bana do', plan([{'op': 'create_category', 'name': 'Y'}]))
        code = self.code(self.reply_text(mo))
        mx = await self.say(self.other, f'server confirm {code}')
        self.assertIn('wahi owner', self.reply_text(mx))
        self.g.create_category.assert_not_called()

    async def test_delete_needs_explicit_selection(self):
        mo = await self.ask(self.owner, 'Kristy purane channel hata do aur rename karo',
                            plan([{'op': 'delete_channel', 'id': 53},
                                  {'op': 'rename', 'kind': 'channel', 'id': 51, 'name': 'main'}]))
        out = self.reply_text(mo)
        self.assertIn('DELETE', out)
        self.assertIn('delete 1', out)
        code = self.code(out)
        await self.say(self.owner, f'Kristy server confirm {code}')
        self.g.chans[53].delete.assert_not_called()      # plain confirm never deletes
        self.g.chans[51].edit.assert_awaited_once()
        mo = await self.ask(self.owner, 'Kristy ek baar phir server ka old channel delete plan', plan([{'op': 'delete_channel', 'id': 53}]))
        code = self.code(self.reply_text(mo))
        await self.say(self.owner, f'Kristy server confirm {code} delete 1')
        self.g.chans[53].delete.assert_awaited_once()

    async def test_stale_state_refused(self):
        mo = await self.ask(self.owner, 'Kristy server ka general channel rename karo',
                            plan([{'op': 'rename', 'kind': 'channel', 'id': 51, 'name': 'main'}]))
        self.g.chans[51].name = 'someone-renamed'
        m2 = await self.say(self.owner, f'Kristy server confirm {self.code(self.reply_text(mo))}')
        self.g.chans[51].edit.assert_not_called()
        self.assertIn('NOT done', self.reply_text(m2))

    async def test_partial_failure_reported(self):
        self.g.create_text_channel.side_effect = discord.Forbidden(MagicMock(status=403, reason='x'), 'no')
        mo = await self.ask(self.owner, 'Kristy server mein category aur channel banao',
                            plan([{'op': 'create_category', 'name': 'A'},
                                  {'op': 'create_channel', 'name': 'b', 'type': 'text', 'category': 'A'},
                                  {'op': 'create_role', 'name': 'C'}]))
        m2 = await self.say(self.owner, f'Kristy server confirm {self.code(self.reply_text(mo))}')
        out = self.reply_text(m2)
        self.assertIn('2. NOT done', out)
        self.assertIn('3. not run', out)
        self.assertIn('1. done', out)
        self.g.create_role.assert_not_called()

    async def test_large_preview_survives_chunking_and_never_applies(self):
        ops = [{'op': 'create_channel', 'name': f'channel-number-{i}', 'type': 'text',
                'topic': 'a topic line that makes the preview long enough'} for i in range(30)]
        mo = await self.ask(self.owner, 'Kristy server ke channels bana do', plan(ops))
        out = self.reply_text(mo) + '\n'.join(str(c.args[0]) for c in self.channel.send.await_args_list)
        self.assertGreater(len(out), 1900)           # really was chunked
        self.assertIn('30. create text channel', out)
        self.assertTrue(self.code(out))
        self.g.create_text_channel.assert_not_called()

    async def test_truncated_plan_never_shown_or_applied(self):
        m = await self.ask(self.owner, 'Kristy server ke channels bana do',
                           '[[do:server plan]]\n{"summary": "x", "ops": [{"op": "create_category", "na')
        out = self.reply_text(m)
        self.assertNotIn('create_category', out)
        self.assertIn('Kuch change nahi hua', out)
        self.g.create_category.assert_not_called()

    async def test_plain_chat_unaffected(self):
        self.script('badhiya! tum batao')
        m = self.msg(self.owner, 'Kristy kaisi ho?', guild=self.g)
        await self.bot.on_message(m)
        self.assertFalse(self.bot._actions.builder.pending)
        self.assertNotIn('SERVER LAYOUT (read-only data', self.last_payload()['system_instruction']['parts'][0]['text'])


if __name__ == '__main__':
    unittest.main()
