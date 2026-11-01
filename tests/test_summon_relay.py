"""Offline tests for nickname teaching and the VC summon relay in bot.py.

Everything is faked: no Discord login, no network, no real pings.
"""
import importlib.util
import os
import pathlib
import tempfile
import types
import unittest
import warnings
from unittest.mock import AsyncMock, patch

ROOT = pathlib.Path(__file__).parents[1]


def load_source(spec, module):
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message=r"^'asyncio\.iscoroutinefunction' is deprecated and slated for removal in Python 3\.16; use inspect\.iscoroutinefunction\(object\) instead$|^'asyncio\.iscoroutinefunction' is deprecated.*$", category=DeprecationWarning)
        spec.loader.exec_module(module)


def member(mid, name, display=None, global_name=None, bot=False):
    return types.SimpleNamespace(
        id=mid, name=name, display_name=display or name,
        global_name=global_name, bot=bot,
        mention=f"<@{mid}>",
    )


class SummonRelay(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        env = {
            'DISCORD_TOKEN': 'OFFLINE_DUMMY', 'GEMINI_API_KEY': 'OFFLINE_DUMMY',
            'MUSIC_ENABLED': '0', 'MUSIC_SYNC_COMMANDS': '0',
            'ALIASES_FILE': str(pathlib.Path(self._tmp.name) / 'aliases.json'),
        }
        with patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location('offline_bot_summon', ROOT / 'bot.py')
            self.bot = importlib.util.module_from_spec(spec)
            load_source(spec, self.bot)
        # aliases stays in sys.modules between per-test bot reimports, so
        # re-point its file at THIS test's temp dir for isolation
        self.bot.aliases._ALIASES_FILE = pathlib.Path(self._tmp.name) / 'aliases.json'
        self.bot.self_diary.PATH = pathlib.Path(self._tmp.name)/'lessons.json'
        self.addAsyncCleanup(self.bot.client.close)
        # a small friend server
        self.fazal = member(1, 'fazal_techiudex07', 'Fazal')
        self.mayank = member(2, 'mayank_123', 'Mayank')
        self.naiyo = member(3, 'naiyo_x', 'Naiyo')
        self.junaid = member(4, 'junaid.k', 'Junaid')
        self.me = member(99, 'kristy_bot', 'Kristy', bot=True)
        self.guild = types.SimpleNamespace(
            id=555,
            members=[self.fazal, self.mayank, self.naiyo, self.junaid, self.me],
            me=self.me,
        )
        self.guild.get_member = lambda uid: next((m for m in self.guild.members if m.id == uid), None)

    def dest(self, perms_bot=(True, True), perms_req=True, ctype=None, guild=None):
        bot_perms = types.SimpleNamespace(view_channel=perms_bot[0], send_messages=perms_bot[1])
        req_perms = types.SimpleNamespace(view_channel=perms_req, send_messages=True)
        def permissions_for(who):
            return bot_perms if who is self.me else req_perms
        return types.SimpleNamespace(
            id=777, guild=guild or self.guild,
            type=ctype if ctype is not None else __import__('discord').ChannelType.text,
            mention='<#777>', permissions_for=permissions_for, send=AsyncMock(),
        )

    def msg(self, content, mentions=(), channel_mentions=(), author=None):
        author = author or self.fazal
        author.voice = getattr(author, 'voice', None)
        m = types.SimpleNamespace(
            guild=self.guild, author=author, content=content,
            mentions=list(mentions), channel_mentions=list(channel_mentions),
            channel=types.SimpleNamespace(id=555000), reply=AsyncMock(),
        )
        return m

    # ---------- parsing ----------
    def test_extract_user_example(self):
        out = self.bot.extract_name_candidates(
            "Kristy <#1438> pe Mayank aur Naiyo aur Junaid ko Vc join karno bolo turant")
        self.assertEqual(out, ["Mayank", "Naiyo", "Junaid"])

    def test_extract_mentions_and_commas(self):
        out = self.bot.extract_name_candidates(
            "Kristy <@2>, Naiyo ko <#1438> mein vc bulao")
        self.assertEqual(out, ["Naiyo"])

    def test_summon_verb_gating(self):
        self.assertTrue(self.bot.SUMMON_VERB_RE.search("#x pe Mayank ko Vc join karno bolo"))
        self.assertTrue(self.bot.SUMMON_VERB_RE.search("Mayank ko bulao"))
        self.assertFalse(self.bot.SUMMON_VERB_RE.search("#general ka kya scene hai aaj"))
        self.assertFalse(self.bot.SUMMON_VERB_RE.search("kaisa chal raha hai"))

    def test_teach_patterns(self):
        m = self.bot.ALIAS_TEACH_RE.search("yaad rakhna Mayank ka username mayank_123")
        self.assertIsNotNone(m)
        self.assertEqual(m.group('username'), 'mayank_123')
        self.assertIn('mayank', m.group('alias').casefold())
        m2 = self.bot.ALIAS_TEACH_RE2.search("Mayank's username is mayank_123")
        self.assertIsNotNone(m2)
        self.assertIsNotNone(self.bot.SELF_ALIAS_RE.search("call me Fazallette"))
        self.assertIsNotNone(self.bot.SELF_ALIAS_RE2.search("mujhe Junaid bhai bulao"))
        self.assertIsNotNone(self.bot.ALIAS_FORGET_RE.search("Mayank ko bhool ja"))
        self.assertIsNotNone(self.bot.ALIAS_FORGET_RE2.search("forget the nickname Mayank"))

    # ---------- alias-aware resolution ----------
    def test_find_tag_target_alias_first_and_stale(self):
        self.bot.aliases.set_alias(555, "miku", 2)
        got, unclear = self.bot.find_tag_target(self.guild, "miku")
        self.assertIs(got, self.mayank)
        self.assertFalse(unclear)
        # stale: alias points at someone no longer in the server
        self.bot.aliases.set_alias(555, "ghost", 424242)
        got, unclear = self.bot.find_tag_target(self.guild, "ghost")
        self.assertIsNone(got)
        self.assertTrue(unclear)

    # ---------- teach / forget handlers ----------
    async def test_handle_alias_teach_success(self):
        m = self.msg("Kristy yaad rakhna Mayank ka username mayank_123")
        handled = await self.bot.handle_alias_teach(m, m.content)
        self.assertTrue(handled)
        m.reply.assert_awaited_once()
        self.assertIn("Mayank", m.reply.await_args.args[0])
        self.assertEqual(self.bot.aliases.resolve(555, "Mayank"), 2)

    async def test_handle_alias_teach_unknown_username(self):
        m = self.msg("Kristy Mayank ka username no_such_user")
        self.assertTrue(await self.bot.handle_alias_teach(m, m.content))
        self.assertIn("clearly nahi mila", m.reply.await_args.args[0])
        self.assertIsNone(self.bot.aliases.resolve(555, "Mayank"))

    async def test_handle_alias_teach_taken_and_reserved(self):
        self.bot.aliases.set_alias(555, "bhai", 2)
        m = self.msg("Kristy bhai ka username naiyo_x")
        await self.bot.handle_alias_teach(m, m.content)
        self.assertIn("already", m.reply.await_args.args[0])
        self.assertEqual(self.bot.aliases.resolve(555, "bhai"), 2)  # not retargeted
        m2 = self.msg("Kristy everyone ka username mayank_123")
        await self.bot.handle_alias_teach(m2, m2.content)
        self.assertIn("common word", m2.reply.await_args.args[0])

    async def test_handle_alias_teach_no_hijack_of_member_name(self):
        m = self.msg("Kristy Junaid ka username mayank_123")
        await self.bot.handle_alias_teach(m, m.content)
        self.assertIn("hijack", m.reply.await_args.args[0])
        self.assertIsNone(self.bot.aliases.resolve(555, "Junaid"))

    async def test_self_alias_and_guard(self):
        m = self.msg("Kristy call me Fazallette")
        self.assertTrue(await self.bot.handle_self_alias(m, m.content))
        self.assertEqual(self.bot.aliases.resolve(555, "Fazallette"), 1)
        m2 = self.msg("Kristy call me when done")
        self.assertFalse(await self.bot.handle_self_alias(m2, m2.content))

    async def test_alias_forget(self):
        self.bot.aliases.set_alias(555, "bhai", 2)
        m = self.msg("Kristy bhai ko bhool ja")
        self.assertTrue(await self.bot.handle_alias_forget(m, m.content))
        self.assertIsNone(self.bot.aliases.resolve(555, "bhai"))

    async def test_alias_forget_pronouns_ignored(self):
        # "mujhe bhool ja" is a DIARY forget, never an alias operation
        self.bot.aliases.set_alias(555, "mujhe", 2)  # even if somehow present
        m = self.msg("Kristy mujhe ko bhool ja")
        # regex needs 'X ko bhool ja' - 'mujhe' is in the skip list regardless
        self.bot.aliases.remove_alias(555, "mujhe")
        self.bot.aliases.set_alias(555, "bhai", 2)
        m2 = self.msg("Kristy unko bhool ja")
        self.assertFalse(await self.bot.handle_alias_forget(m2, m2.content))
        self.assertEqual(self.bot.aliases.resolve(555, "bhai"), 2)

    async def test_no_actions_from_quotes_code_reported_speech_or_negation(self):
        dest = self.dest()
        for text in ['Kristy `Mayank ko vc bulao <#777>`',
                     'Kristy "Mayank ko vc bulao <#777>"',
                     '> Kristy Mayank ko vc bulao <#777>',
                     'Kristy example Mayank ko vc bulao <#777>',
                     'Kristy Mayank ko vc mat bulao <#777>']:
            m = self.msg(text, channel_mentions=[dest])
            await self.bot.handle_summon(m, text)
            dest.send.assert_not_awaited()
        m = self.msg('Kristy Mayank ka username mayank_123')
        m.webhook_id = 123
        self.assertFalse(await self.bot.handle_alias_teach(m, m.content))
        del m.webhook_id
        m.message_snapshots = [object()]
        self.assertFalse(await self.bot.handle_alias_teach(m, m.content))

    async def test_alias_write_failure_not_claimed_saved(self):
        m = self.msg('Kristy Miku ka username mayank_123')
        with patch.object(self.bot.aliases, '_save_all', return_value=False):
            await self.bot.handle_alias_teach(m, m.content)
        self.assertIn('save nahi hua', m.reply.await_args.args[0])
        self.assertIsNone(self.bot.aliases.resolve(555, 'Miku'))

    async def test_requester_no_send_permission_rejected(self):
        dest = self.dest()
        dest.permissions_for = lambda who: types.SimpleNamespace(
            view_channel=True, send_messages=who is self.me)
        m = self.msg('Kristy <#777> pe Mayank ko vc bulao', channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_not_awaited()

    async def test_missing_bot_member_rejected(self):
        dest = self.dest()
        self.guild.me = None
        m = self.msg('Kristy <#777> pe Mayank ko vc bulao', channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_not_awaited()

    async def test_self_diary_review_and_exact_owner_id(self):
        self.bot.self_diary.PATH = pathlib.Path(self._tmp.name)/'lessons.json'
        self.bot.OWNER_ID = '1'
        m=self.msg('Kristy lesson suggest concise-answer');m.id=100
        self.assertTrue(await self.bot.handle_self_diary(m,m.content))
        self.assertEqual(self.bot.self_diary.prompt(555),'')
        other=self.msg('Kristy lesson approve 1',author=self.mayank);other.id=101
        await self.bot.handle_self_diary(other,other.content)
        self.assertEqual(self.bot.self_diary.prompt(555),'')
        m.content='Kristy lesson approve 1'
        await self.bot.handle_self_diary(m,m.content)
        self.assertIn('concise',self.bot.self_diary.prompt(555))
        self.bot.OWNER_ID=''
        m.content='Kristy lesson reset confirm'
        await self.bot.handle_self_diary(m,m.content)
        self.assertIn('concise',self.bot.self_diary.prompt(555))

    async def test_self_diary_quotes_webhooks_and_unknown_lessons_ignored(self):
        self.bot.self_diary.PATH = pathlib.Path(self._tmp.name)/'lessons.json'
        for text in ['Kristy `lesson suggest concise-answer`', '> Kristy lesson suggest concise-answer']:
            m=self.msg(text);m.id=1
            self.assertFalse(await self.bot.handle_self_diary(m,text))
        m=self.msg('Kristy lesson suggest concise-answer');m.id=1;m.webhook_id=7
        self.assertFalse(await self.bot.handle_self_diary(m,m.content))
        self.assertEqual(self.bot.self_diary.load()['entries'],[])

    async def test_roster_direct_answer_has_no_pings_and_no_brain(self):
        m=self.msg('Kristy VC mein kaun hai?');m.attachments=[];m.stickers=[];m.embeds=[];m.reference=None
        self.bot.client.music=None
        with patch.object(self.bot.voice_roster,'snapshot',return_value='Gaming: @everyone and @here') as lookup, patch.object(self.bot,'think',new_callable=AsyncMock) as brain:
            await self.bot.on_message(m)
        lookup.assert_called_once()
        brain.assert_not_called()
        m.reply.assert_awaited_once()
        mentions=m.reply.call_args.kwargs['allowed_mentions']
        self.assertFalse(mentions.everyone);self.assertFalse(mentions.users);self.assertFalse(mentions.roles)

    async def test_roster_quote_and_forward_not_read(self):
        for text in ('Kristy `VC mein kaun hai?`', '> Kristy VC mein kaun hai?'):
            m=self.msg(text)
            self.assertIsNone(self.bot.action_text(m,text))
        m=self.msg('Kristy VC mein kaun hai?');m.message_snapshots=[object()]
        self.assertIsNone(self.bot.action_text(m,m.content))

    # ---------- summon relay ----------
    async def test_summon_success_names_only(self):
        dest = self.dest()
        m = self.msg("Kristy <#777> pe Mayank aur Naiyo aur Junaid ko Vc join karno bolo turant",
                     channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_awaited_once()
        text, kwargs = dest.send.await_args.args[0], dest.send.await_args.kwargs
        for mid in (2, 3, 4):
            self.assertIn(f"<@{mid}>", text)
        self.assertNotIn("@everyone", text)
        self.assertNotIn("@here", text)
        am = kwargs["allowed_mentions"]
        self.assertFalse(am.everyone)
        self.assertFalse(am.roles)
        self.assertEqual({u.id for u in am.users}, {2, 3, 4})
        self.assertIn("<#777>", m.reply.await_args.args[0])

    async def test_summon_success_mentions_and_voice(self):
        dest = self.dest()
        self.fazal.voice = types.SimpleNamespace(channel=types.SimpleNamespace(name='Gaming Lounge'))
        m = self.msg("Kristy <#777> pe <@2> @naiyo ko vc bulao",
                     mentions=[self.mayank, self.naiyo], channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_awaited_once()
        text = dest.send.await_args.args[0]
        self.assertIn("<@2>", text)
        self.assertIn("<@3>", text)
        self.assertIn("Gaming Lounge VC", text)
        self.assertIn("Fazal bula rahe hain", text)

    async def test_summon_unclear_name_blocks_everything(self):
        dest = self.dest()
        m = self.msg("Kristy <#777> pe Mayank aur Zarqoon ko vc bulao", channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_not_awaited()
        self.assertIn("Zarqoon", m.reply.await_args.args[0])

    async def test_summon_cross_guild_rejected(self):
        other = types.SimpleNamespace(id=999)
        dest = self.dest(guild=other)
        m = self.msg("Kristy <#777> pe Mayank ko vc bulao", channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_not_awaited()
        self.assertIn("is server ka nahi", m.reply.await_args.args[0])

    async def test_summon_nontext_channel_rejected(self):
        import discord
        dest = self.dest(ctype=discord.ChannelType.voice)
        m = self.msg("Kristy <#777> pe Mayank ko vc bulao", channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_not_awaited()
        self.assertIn("text nahi bhej sakti", m.reply.await_args.args[0])

    async def test_summon_bot_perms_rejected(self):
        dest = self.dest(perms_bot=(True, False))
        m = self.msg("Kristy <#777> pe Mayank ko vc bulao", channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_not_awaited()
        self.assertIn("permission nahi", m.reply.await_args.args[0])

    async def test_summon_requester_access_rejected(self):
        dest = self.dest(perms_req=False)
        m = self.msg("Kristy <#777> pe Mayank ko vc bulao", channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_not_awaited()
        self.assertIn("dekh nahi sakte", m.reply.await_args.args[0])

    async def test_summon_multi_channel_asks(self):
        d1, d2 = self.dest(), self.dest()
        m = self.msg("Kristy <#777> <#778> pe Mayank ko vc bulao", channel_mentions=[d1, d2])
        await self.bot.handle_summon(m, m.content)
        d1.send.assert_not_awaited()
        d2.send.assert_not_awaited()
        self.assertIn("ek hi #channel", m.reply.await_args.args[0])

    async def test_summon_no_names_asks(self):
        dest = self.dest()
        m = self.msg("Kristy <#777> pe vc join karne bolo", channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_not_awaited()
        self.assertIn("kise bulaana hai", m.reply.await_args.args[0])

    async def test_summon_ping_cap(self):
        dest = self.dest()
        extra = [member(10 + i, f"user{i}") for i in range(5)]
        self.guild.members.extend(extra)
        names = " aur ".join(["Mayank"] + [f"user{i}" for i in range(5)])
        m = self.msg(f"Kristy <#777> pe {names} ko vc bulao", channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_not_awaited()
        self.assertIn("max 5", m.reply.await_args.args[0])

    async def test_summon_cooldown(self):
        dest = self.dest()
        m = self.msg("Kristy <#777> pe Mayank ko vc bulao", channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        await self.bot.handle_summon(m, m.content)
        self.assertEqual(dest.send.await_count, 1)
        self.assertIn("ruk jao", m.reply.await_args.args[0])

    async def test_summon_via_taught_alias(self):
        dest = self.dest()
        self.bot.aliases.set_alias(555, "miku", 2)
        m = self.msg("Kristy <#777> pe miku ko vc bulao", channel_mentions=[dest])
        await self.bot.handle_summon(m, m.content)
        dest.send.assert_awaited_once()
        self.assertIn("<@2>", dest.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
