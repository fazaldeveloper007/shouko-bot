import json
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

import actions
import server_builder as sb

OWNER = 111
OTHER = 222


def role(id, name, pos, managed=False, default=False, members=0):
    r = MagicMock(spec=discord.Role)
    r.id, r.name, r.position, r.managed = id, name, pos, managed
    r.is_default.return_value = default
    r.members = [object()] * members
    r.permissions = discord.Permissions.none()
    r.edit = AsyncMock()
    r.delete = AsyncMock()
    return r


def chan(cls, id, name, category=None):
    c = MagicMock(spec=cls)
    c.id, c.name, c.category = id, name, category
    c.edit = AsyncMock()
    c.delete = AsyncMock()
    c.set_permissions = AsyncMock()
    c.overwrites_for = lambda r: discord.PermissionOverwrite()
    c.channels = []
    return c


def make_guild(owner_id=OWNER, admin=True):
    everyone = role(1, '@everyone', 0, default=True)
    botrole = role(2, 'Kristy', 10)
    low = role(3, 'Member', 5, members=7)
    high = role(4, 'Boss', 20)
    cat = chan(discord.CategoryChannel, 50, 'Chat')
    gen = chan(discord.TextChannel, 51, 'general', cat)
    rules = chan(discord.TextChannel, 52, 'rules')
    old = chan(discord.TextChannel, 53, 'old-stuff', cat)
    cat.channels = [gen, old]
    me = types.SimpleNamespace(top_role=botrole, roles=[botrole, everyone],
                               guild_permissions=discord.Permissions(administrator=admin, manage_channels=True, manage_roles=True))
    chans = {c.id: c for c in (cat, gen, rules, old)}
    roles = {r.id: r for r in (everyone, botrole, low, high)}
    g = types.SimpleNamespace(
        id=10, owner_id=owner_id, name='Test', member_count=9, me=me, default_role=everyone,
        roles=list(roles.values()), channels=list(chans.values()), categories=[cat],
        get_channel=chans.get, get_role=roles.get, rules_channel=rules,
        public_updates_channel=None, system_channel=None, afk_channel=None,
        create_category=AsyncMock(side_effect=lambda n, reason=None: chan(discord.CategoryChannel, 90, n)),
        create_text_channel=AsyncMock(side_effect=lambda n, **k: chan(discord.TextChannel, 91, n)),
        create_voice_channel=AsyncMock(side_effect=lambda n, **k: chan(discord.VoiceChannel, 92, n)),
        create_role=AsyncMock(side_effect=lambda **k: role(95, k['name'], 3)),
    )
    g.chans, g.roles_by_id = chans, roles
    return g


def msg(g, author=OWNER, channel=7):
    ch = types.SimpleNamespace(id=channel)
    return types.SimpleNamespace(author=types.SimpleNamespace(id=author), channel=ch, guild=g,
                                 reply=AsyncMock(), content='')


def plan(ops, summary='s'):
    return '[[do:server plan]]\n' + json.dumps({'summary': summary, 'ops': ops})


def code_of(preview):
    return preview.split('server confirm ')[1][:10]


class BuilderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.g = make_guild()
        self.b = sb.Builder(None)

    async def confirm(self, text, author=OWNER, g=None):
        m = msg(g or self.g, author)
        ok = await self.b.handle(m, text)
        return ok, m

    async def test_non_plan_text_passes(self):
        self.assertIsNone(self.b.plan(msg(self.g), 'hello'))

    async def test_only_real_guild_owner_can_plan(self):
        out = self.b.plan(msg(self.g, OTHER), plan([{'op': 'create_category', 'name': 'x'}]))
        self.assertIn('actual owner', out)
        self.assertEqual(self.b.pending, {})

    async def test_owner_id_must_be_the_guild_owner_not_config(self):
        g = make_guild(owner_id=OTHER)  # OWNER is not owner of this guild
        out = self.b.plan(msg(g, OWNER), plan([{'op': 'create_category', 'name': 'x'}]))
        self.assertIn('actual owner', out)

    async def test_preview_changes_nothing_and_confirm_creates(self):
        out = self.b.plan(msg(self.g), plan([
            {'op': 'create_category', 'name': 'Games'},
            {'op': 'create_channel', 'name': 'Chat Room', 'type': 'text', 'category': 'Games', 'topic': 'hi @everyone'},
            {'op': 'create_role', 'name': 'Gamer', 'color': '#ff0000'}]))
        self.assertIn('NAHI hua', out)
        self.g.create_category.assert_not_called()
        code = code_of(out)
        ok, m = await self.confirm(f'server confirm {code}')
        self.assertTrue(ok)
        self.g.create_category.assert_awaited_once()
        kw = self.g.create_text_channel.await_args
        self.assertEqual(kw.args[0], 'chat-room')
        self.assertNotIn('@everyone', kw.kwargs['topic'])
        self.assertEqual(self.g.create_role.await_args.kwargs['permissions'], discord.Permissions.none())
        self.assertIn('3 steps done', m.reply.await_args.args[0])

    async def test_non_owner_confirm_refused_and_plan_kept(self):
        out = self.b.plan(msg(self.g), plan([{'op': 'create_category', 'name': 'Games'}]))
        ok, m = await self.confirm(f'server confirm {code_of(out)}', author=OTHER)
        self.assertTrue(ok)
        self.g.create_category.assert_not_called()
        self.assertEqual(len(self.b.pending), 1)

    async def test_ownership_transfer_before_confirm_blocks(self):
        out = self.b.plan(msg(self.g), plan([{'op': 'create_category', 'name': 'Games'}]))
        self.g.owner_id = OTHER
        await self.confirm(f'server confirm {code_of(out)}')
        self.g.create_category.assert_not_called()

    async def test_single_use(self):
        out = self.b.plan(msg(self.g), plan([{'op': 'create_category', 'name': 'Games'}]))
        c = code_of(out)
        await self.confirm(f'server confirm {c}')
        ok, m = await self.confirm(f'server confirm {c}')
        self.assertEqual(self.g.create_category.await_count, 1)
        self.assertIn('nahi mila', m.reply.await_args.args[0])

    async def test_cancel(self):
        out = self.b.plan(msg(self.g), plan([{'op': 'create_category', 'name': 'Games'}]))
        await self.confirm(f'server cancel {code_of(out)}')
        self.g.create_category.assert_not_called()
        self.assertEqual(self.b.pending, {})

    async def test_plain_confirm_never_deletes(self):
        out = self.b.plan(msg(self.g), plan([{'op': 'delete_channel', 'id': 53},
                                             {'op': 'rename', 'kind': 'channel', 'id': 51, 'name': 'main'}]))
        self.assertIn('DELETE', out)
        await self.confirm(f'server confirm {code_of(out)}')
        self.g.chans[53].delete.assert_not_called()
        self.g.chans[51].edit.assert_awaited_once()

    async def test_delete_needs_its_number(self):
        out = self.b.plan(msg(self.g), plan([{'op': 'delete_channel', 'id': 53}]))
        c = code_of(out)
        ok, m = await self.confirm(f'server confirm {c} delete 2')
        self.g.chans[53].delete.assert_not_called()
        self.assertIn('galat', m.reply.await_args.args[0])
        # plan was consumed by the bad confirm; build again
        out = self.b.plan(msg(self.g), plan([{'op': 'delete_channel', 'id': 53}]))
        await self.confirm(f'server confirm {code_of(out)} delete 1')
        self.g.chans[53].delete.assert_awaited_once()

    async def test_skip_steps(self):
        out = self.b.plan(msg(self.g), plan([{'op': 'rename', 'kind': 'channel', 'id': 51, 'name': 'a'},
                                             {'op': 'set_topic', 'id': 51, 'topic': 't'}]))
        await self.confirm(f'server confirm {code_of(out)} skip 1')
        self.assertEqual(self.g.chans[51].edit.await_count, 1)
        self.assertEqual(self.g.chans[51].edit.await_args.kwargs.get('topic'), 't')

    async def test_rejects_dangerous_permissions(self):
        for perm in ('administrator', 'manage_roles', 'ban_members', 'mention_everyone', 'kick_members'):
            out = self.b.plan(msg(self.g), plan([{'op': 'role_perms', 'id': 3, 'allow': [perm]}]))
            self.assertIn('valid nahi', out, perm)
            out = self.b.plan(msg(self.g), plan([{'op': 'set_overwrite', 'channel': 51, 'role': 3, 'allow': [perm]}]))
            self.assertIn('valid nahi', out, perm)

    async def test_role_hierarchy_and_special_roles_protected(self):
        for rid in (1, 2, 4):  # everyone, bot's own top role, higher role
            out = self.b.plan(msg(self.g), plan([{'op': 'delete_role', 'id': rid}]))
            self.assertIn('valid nahi', out, rid)
        self.assertIn('valid nahi', self.b.plan(msg(self.g), plan([{'op': 'edit_role', 'id': 4, 'color': '#ffffff'}])))

    async def test_system_channel_not_deletable(self):
        out = self.b.plan(msg(self.g), plan([{'op': 'delete_channel', 'id': 52}]))
        self.assertIn('valid nahi', out)

    async def test_category_with_channels_not_deletable_until_emptied(self):
        self.assertIn('valid nahi', self.b.plan(msg(self.g), plan([{'op': 'delete_category', 'id': 50}])))
        ok = self.b.plan(msg(self.g), plan([{'op': 'delete_channel', 'id': 51}, {'op': 'delete_channel', 'id': 53},
                                            {'op': 'delete_category', 'id': 50}]))
        self.assertIn('DELETE', ok)

    async def test_unknown_id_and_guess_names_for_existing(self):
        self.assertIn('valid nahi', self.b.plan(msg(self.g), plan([{'op': 'rename', 'kind': 'channel', 'id': 999, 'name': 'x'}])))
        self.assertIn('valid nahi', self.b.plan(msg(self.g), plan([{'op': 'delete_channel', 'id': 'old-stuff'}])))
        self.assertIn('valid nahi', self.b.plan(msg(self.g), plan([{'op': 'rename', 'kind': 'category', 'id': 51, 'name': 'x'}])))

    async def test_forward_reference_rejected(self):
        out = self.b.plan(msg(self.g), plan([
            {'op': 'create_channel', 'name': 'x', 'category': 'Later'},
            {'op': 'create_category', 'name': 'Later'}]))
        self.assertIn('valid nahi', out)

    async def test_stale_server_blocks_step(self):
        out = self.b.plan(msg(self.g), plan([{'op': 'rename', 'kind': 'channel', 'id': 51, 'name': 'main'}]))
        self.g.chans[51].name = 'renamed-by-someone'
        ok, m = await self.confirm(f'server confirm {code_of(out)}')
        self.g.chans[51].edit.assert_not_called()
        self.assertIn('NOT done', m.reply.await_args.args[0])

    async def test_stops_after_first_failure(self):
        self.g.create_category.side_effect = discord.Forbidden(MagicMock(status=403, reason='x'), 'no')
        out = self.b.plan(msg(self.g), plan([{'op': 'create_category', 'name': 'A'}, {'op': 'create_role', 'name': 'B'}]))
        ok, m = await self.confirm(f'server confirm {code_of(out)}')
        self.g.create_role.assert_not_called()
        text = m.reply.await_args.args[0]
        self.assertIn('NOT done', text)
        self.assertIn('not run', text)

    async def test_missing_bot_permission_reported(self):
        g = make_guild(admin=False)
        g.me.guild_permissions = discord.Permissions(manage_roles=True)
        b = sb.Builder(None)
        out = b.plan(msg(g), plan([{'op': 'create_category', 'name': 'A'}]))
        m = msg(g)
        await b.handle(m, f'server confirm {code_of(out)}')
        g.create_category.assert_not_called()
        self.assertIn('manage_channels', m.reply.await_args.args[0])

    async def test_overwrite_hiding_channel_keeps_bot_access_and_is_disclosed(self):
        out = self.b.plan(msg(self.g), plan([{'op': 'set_overwrite', 'channel': 51, 'role': '@everyone', 'deny': ['view_channel']}]))
        self.assertIn('lock-out', out)
        await self.confirm(f'server confirm {code_of(out)}')
        self.assertEqual(self.g.chans[51].set_permissions.await_count, 2)

    async def test_too_many_ops_and_bad_json(self):
        ops = [{'op': 'create_category', 'name': f'c{i}'} for i in range(sb.MAX_OPS + 1)]
        self.assertIn('max', self.b.plan(msg(self.g), plan(ops)))
        self.assertIn('valid nahi', self.b.plan(msg(self.g), '[[do:server plan]]\n{"ops": []}'))

    async def test_layout_only_for_owner_and_has_ids(self):
        self.b.prime_layout(msg(self.g, OTHER), 'change the server roles')
        self.assertEqual(self.b.layout_note(7), '')
        self.b.prime_layout(msg(self.g, OWNER), 'hi there')
        self.assertEqual(self.b.layout_note(7), '')
        self.b.prime_layout(msg(self.g, OWNER), 'organise the server channels')
        note = self.b.layout_note(7)
        self.assertIn('51', note)
        self.assertIn('ABOVE-BOT', note)


class WiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_actions_routes_plan_and_confirm(self):
        g = make_guild()
        a = actions.Actions(types.SimpleNamespace(), '999', types.SimpleNamespace())
        out = await a.maybe_execute(msg(g), plan([{'op': 'create_category', 'name': 'Z'}]))
        self.assertIn('NAHI hua', out)
        m = msg(g)
        self.assertTrue(await a.handle(m, f'server confirm {code_of(out)}'))
        g.create_category.assert_awaited_once()

    async def test_capability_mentions_builder(self):
        self.assertIn('SERVER BUILDER', actions.CAPABILITY)


if __name__ == '__main__':
    unittest.main()
