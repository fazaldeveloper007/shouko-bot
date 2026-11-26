import datetime as dt
import pathlib
import tempfile
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import discord
import timed_messages as tm
from test_reminder_intent import Base


class RoleBase(Base):
    def setUp(self):
        super().setUp()
        self.role = S(id=501, name='VC Member', mentionable=True, is_default=lambda: False)
        self.guild.roles = [self.role]
        self.guild.get_role = lambda i: {501: self.role}.get(i)


class RolePings(RoleBase):
    async def test_plain_role_name_after_ping_colon_saves_with_role_target(self):
        # The exact live failure: 'Ping: VC Member (role)' used to loop forever.
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m = self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m, 'Set daily reminder at 9 PM for VC invite. Ping: VC Member (role)')
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        e = rows[0]
        self.assertEqual((e['status'], e['targets'], e.get('roles')), ('scheduled', [1], [501]))
        self.assertTrue(e['body'].startswith('<@1> <@&501>\n'), e['body'])
        self.assertIn('Repeating reminder saved', m.reply.call_args.args[0])

    async def test_suggested_answer_tag_role_name_also_saves(self):
        # The clarification tells the user to write 'tag VC Member'; that answer
        # must now succeed instead of looping.
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m = self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m, 'Set daily reminder at 9 PM for VC invite. tag VC Member')
        self.assertEqual(len(self.store.load()), 1)
        self.assertEqual(self.store.load()[0].get('roles'), [501])

    async def test_hindi_postposition_answer_saves(self):
        # 'VC Member ko tag karna hai' as the follow-up answer.
        t0 = dt.datetime.now(dt.timezone.utc)
        self.ai(dict(action='need_info', question='Kisko tag karna hai?'),
                dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m1 = self.msg(t0)
        await self.run_msg(m1, 'Set daily reminder at 9 PM for VC invite')
        self.assertEqual(self.store.load(), [])
        m2 = self.msg(t0 + dt.timedelta(seconds=30), ref=S(message_id=700))
        await self.run_msg(m2, 'VC Member ko tag karna hai')
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get('roles'), [501])

    async def test_native_role_mention_with_postposition_tag_word_saves(self):
        # Junaid's phrasing: 'reminder set kardo @VC Member yeh wala tag use karke',
        # with a 9.00 pm dot clock.
        anchor = dt.datetime.now(dt.timezone.utc)
        target = anchor.astimezone(tm.IST) + dt.timedelta(minutes=30)
        hh = target.strftime('%I').lstrip('0'); mm = target.strftime('%M'); ap = target.strftime('%p')
        self.ai(dict(action='remind', when=f'aaj {hh}:{mm} {ap}', task='VC invite', tag_names=[]))
        m = self.msg(anchor)
        await self.run_msg(m, f'Kristy aaj {hh}.{mm} {ap.lower()} ka reminder set kardo <@&501> yeh wala tag use karke for VC invite')
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['targets'], rows[0].get('roles')), ([1], [501]))
        self.assertEqual(m.reply.call_args.args[0], 'OK')

    async def test_native_role_mention_without_tag_word_not_silently_saved(self):
        self.ai(dict(action='remind', when='every monday 8:00 PM', task='save resin for weekly bosses', tag_names=[]))
        m = self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m, 'remind <@&501> every Monday to save resin for weekly bosses at 8 PM')
        self.assertEqual(self.store.load(), [])
        self.assertIn('tag @role', m.reply.call_args.args[0])

    async def test_role_target_never_silently_downgraded_to_requester(self):
        # Unknown role id: still no author-only success, and the reason is stated.
        self.ai(dict(action='need_info', question='Kab?'),
                dict(action='remind', when='10 minute baad', task='drink water', tag_names=[]))
        t0 = dt.datetime.now(dt.timezone.utc)
        m1 = self.msg(t0)
        await self.run_msg(m1, 'remind me to drink water')
        m2 = self.msg(t0 + dt.timedelta(seconds=30), ref=S(message_id=700))
        await self.run_msg(m2, '10 minute baad tag <@&999>')
        self.assertEqual(self.store.load(), [])
        self.assertIn('Role ab server mein verify nahi hua', m2.reply.call_args.args[0])

    async def test_unknown_role_name_states_clear_reason(self):
        self.ai(dict(action='remind', when='5 minute baad', task='standup', tag_names=['Ghost Role']))
        m = self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m, 'remind me and tag Ghost Role in 5 minute for standup')
        self.assertEqual(self.store.load(), [])
        self.assertIn('member ya role nahi mila', m.reply.call_args.args[0])

    async def test_unmentionable_role_refused_at_save_time(self):
        self.role.mentionable = False
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m = self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m, 'Set daily reminder at 9 PM for VC invite. Ping: VC Member (role)')
        self.assertEqual(self.store.load(), [])
        self.assertIn('mentionable nahi', m.reply.call_args.args[0])

    async def test_voice_channel_destination_refused_at_save_time(self):
        vc = S(id=30, guild=self.guild, type=discord.ChannelType.voice, name='Gaming')
        self.guild.get_channel = lambda c: {20: self.dest, 30: vc}.get(c)
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=[]))
        m = self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m, 'Set daily reminder at 9 PM in <#30> for VC invite')
        self.assertEqual(self.store.load(), [])
        self.assertIn('text channel', m.reply.call_args.args[0])

    async def test_model_invented_role_name_still_clarifies(self):
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m = self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m, 'Set daily reminder at 9 PM for VC invite')
        self.assertEqual(self.store.load(), [])
        self.assertIn('tag VC Member', m.reply.call_args.args[0])


class ConditionalRequests(RoleBase):
    async def test_conditional_request_gets_truthful_answer_not_generic_failure(self):
        # 'remind me at 9:00 PM to join VC if I don't reply in 5 mins ...':
        # the condition must not be silently dropped and the time is not missing.
        self.ai(dict(action='remind', when='aaj 9:00 PM', task='join VC', tag_names=[]))
        m = self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m, "Set a reminder and remind me at 9:00 PM to join VC if I don't reply in 5 mins then just message in this chat I won't be coming")
        self.assertEqual(self.store.load(), [])
        reply = m.reply.call_args.args[0]
        self.assertIn('supported nahi', reply)
        self.assertNotIn('Time aur kya yaad dilana', reply)

    async def test_consent_followup_saves_plain_reminder(self):
        t0 = dt.datetime.now(dt.timezone.utc)
        target = t0.astimezone(tm.IST) + dt.timedelta(minutes=30)
        hh = target.strftime('%I').lstrip('0'); mm = target.strftime('%M'); ap = target.strftime('%p')
        self.ai(dict(action='remind', when=f'aaj {hh}:{mm} {ap}', task='join VC', tag_names=[]))
        m1 = self.msg(t0)
        await self.run_msg(m1, f"remind me at {hh}:{mm} {ap} to join VC if I don't reply in 5 mins then message here I won't be coming")
        self.assertEqual(self.store.load(), [])
        self.assertIn('supported nahi', m1.reply.call_args.args[0])
        m2 = self.msg(t0 + dt.timedelta(seconds=30), ref=S(message_id=700))
        await self.run_msg(m2, 'haan bina condition ka set kar do')
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['core'], 'join VC')
        self.assertEqual(m2.reply.call_args.args[0], 'OK')

    async def test_conditional_task_text_not_saved(self):
        t0 = dt.datetime.now(dt.timezone.utc)
        self.ai(dict(action='need_info', question='Kya yaad dilana hai?'),
                dict(action='remind', when='10 minute baad', task='join VC if I do not reply then message here', tag_names=[]))
        m1 = self.msg(t0)
        await self.run_msg(m1, 'remind me 10 minute baad')
        m2 = self.msg(t0 + dt.timedelta(seconds=30), ref=S(message_id=700))
        await self.run_msg(m2, 'to join VC')
        self.assertEqual(self.store.load(), [])
        self.assertIn('condition', m2.reply.call_args.args[0])


class FollowupTime(RoleBase):
    async def test_dot_clock_followup_completes_pending_request(self):
        # Junaid's flow: vague 'lekin 5 min se jaaunga' gets a time question,
        # then '5.05Pm ko' must complete it instead of failing again.
        t0 = dt.datetime.now(dt.timezone.utc)
        target = t0.astimezone(tm.IST) + dt.timedelta(minutes=30)
        hh = target.strftime('%I').lstrip('0'); mm = target.strftime('%M'); ap = target.strftime('%p')
        self.ai(dict(action='need_info', question='Kab?'),
                dict(action='remind', when=f'aaj {hh}:{mm} {ap}', task='paani pene jaana', tag_names=[]))
        m1 = self.msg(t0)
        await self.run_msg(m1, 'mujhe paani pene jaana h lekin 5 min se jaaunga toh mujhe yaad dila dena')
        self.assertEqual(self.store.load(), [])
        m2 = self.msg(t0 + dt.timedelta(seconds=30), ref=S(message_id=700))
        await self.run_msg(m2, f'{hh}.{mm}{ap} ko')
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['core'], 'paani pene jaana')


class RoleDispatch(RoleBase):
    def setUp(self):
        super().setUp()
        self.guild2 = S(id=10, unavailable=False, me=S(id=99))
        self.guild2.get_member = lambda i: {1: self.author, 99: self.guild2.me}.get(i)
        self.guild2.get_channel = lambda c: self.dest if c == 20 else None
        self.guild2.get_role = lambda i: {501: self.role}.get(i)
        self.dest.guild = self.guild2
        self.dest.send = AsyncMock(return_value=S(id=77))
        self.client = S(is_ready=lambda: True, get_guild=lambda gid: self.guild2 if gid == 10 else None)
        self.wstore = tm.Store(pathlib.Path(self.tmp.name) / 'q.json')

    async def test_worker_sends_role_ping_with_allowed_mentions(self):
        e = self.wstore.create(guild=10, author=1, source=20, dest=20, targets=[1],
                               roles=[501], body='<@1> <@&501>\nVC invite', due=100, now=0)
        self.wstore.control(e['id'], 10, 1, 'confirm', 1)
        worker = tm.Worker(self.client, self.wstore)
        await worker.tick(100); await worker.tick(100)
        self.dest.send.assert_awaited_once()
        allowed = self.dest.send.call_args.kwargs['allowed_mentions']
        self.assertEqual([r.id for r in allowed.roles], [501])
        self.assertIn('<@&501>', self.dest.send.call_args.args[0])
        self.assertEqual(self.wstore.load()[0]['status'], 'sent')

    async def test_worker_fails_loudly_when_role_becomes_unmentionable(self):
        e = self.wstore.create(guild=10, author=1, source=20, dest=20, targets=[1],
                               roles=[501], body='<@1> <@&501>\nVC invite', due=100, now=0)
        self.wstore.control(e['id'], 10, 1, 'confirm', 1)
        self.role.mentionable = False
        worker = tm.Worker(self.client, self.wstore)
        await worker.tick(100)
        self.dest.send.assert_not_awaited()
        self.assertEqual(self.wstore.load()[0]['status'], 'failed')

    def test_store_validates_roles(self):
        self.wstore.create(guild=10, author=1, source=20, dest=20, targets=[1],
                           roles=[501], body='<@1> <@&501>\nhi', due=100, now=0)
        data = self.wstore.load()
        self.assertEqual(data[0]['roles'], [501])
        data[0]['roles'] = 'oops'
        self.wstore.path.write_text(__import__('json').dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.wstore.load()

    def test_announcement_with_roles_invalid(self):
        self.wstore.create(guild=10, author=1, source=20, dest=20, targets=[], roles=[501],
                           body='@everyone hi', due=100, now=0, kind='announcement', ping=True)
        with self.assertRaises(ValueError):
            self.wstore.load()

class TagConsent(RoleBase):
    async def test_walo_ko_phrasing_saves_first_turn(self):
        # Live phrasing: 'VC member role walo ko' - no clarify needed.
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m = self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m, 'Set daily reminder at 9 PM for VC invite, VC member role walo ko')
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get('roles'), [501])

    async def test_affirmative_answer_to_tag_question_saves(self):
        # The live loop: Kristy asks '"VC member" ko tag karna hai?', user says 'Haa'.
        t0 = dt.datetime.now(dt.timezone.utc)
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']),
                dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m1 = self.msg(t0)
        await self.run_msg(m1, 'Set daily reminder at 9 PM for VC invite')
        self.assertEqual(self.store.load(), [])
        self.assertIn('tag karna hai', m1.reply.call_args.args[0])
        m2 = self.msg(t0 + dt.timedelta(seconds=30), ref=S(message_id=700))
        await self.run_msg(m2, 'Haa')
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get('roles'), [501])

    async def test_bare_role_name_echo_saves(self):
        # Answering the tag question with just the role name also counts.
        t0 = dt.datetime.now(dt.timezone.utc)
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']),
                dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m1 = self.msg(t0)
        await self.run_msg(m1, 'Set daily reminder at 9 PM for VC invite')
        m2 = self.msg(t0 + dt.timedelta(seconds=30), ref=S(message_id=700))
        await self.run_msg(m2, 'VC member')
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get('roles'), [501])

    async def test_declining_tag_saves_plain_reminder_without_role(self):
        t0 = dt.datetime.now(dt.timezone.utc)
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']),
                dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=[]))
        m1 = self.msg(t0)
        await self.run_msg(m1, 'Set daily reminder at 9 PM for VC invite')
        m2 = self.msg(t0 + dt.timedelta(seconds=30), ref=S(message_id=700))
        await self.run_msg(m2, 'nahi, sirf mujhe yaad dilana')
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get('roles') or [], [])

class ChatContinuity(RoleBase):
    def _with_history(self, lines):
        self.cmd.history_provider = lambda message: list(lines)

    async def test_set_kardo_after_restatement_saves_with_role(self):
        # The live gap: user stated the plan, Kristy restated it, user says
        # 'Yoh fir set kardo reminder' - it must save, not re-ask everything.
        t0 = dt.datetime.now(dt.timezone.utc)
        self._with_history([
            ('Fazallette', 1, '9 PM daily, VC member role walo ko invite bhejna'),
            ('Kristy', None, 'Roz raat 9 baje VC member role ko tag karke gaming ke liye invite karna hai na?'),
        ])
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m = self.msg(t0)
        await self.run_msg(m, 'Yoh fir set kardo reminder')
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get('roles'), [501])
        self.assertIn('Repeating reminder saved', m.reply.call_args.args[0])

    async def test_bot_lines_never_validate_tag_wording(self):
        # Kristy's own restatement must NOT authorize a role ping by itself.
        t0 = dt.datetime.now(dt.timezone.utc)
        self._with_history([
            ('Kristy', None, 'roz raat 9 baje tag VC Member karke invite karna hai na?'),
        ])
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m = self.msg(t0)
        await self.run_msg(m, 'haan set kardo reminder')
        self.assertEqual(self.store.load(), [])

    async def test_other_members_lines_never_validate(self):
        # Another member's plan in chat is not THIS requester's wording.
        t0 = dt.datetime.now(dt.timezone.utc)
        self._with_history([
            ('Junaid', 2, '9 PM daily, VC member role walo ko invite bhejna'),
        ])
        self.ai(dict(action='remind', when='every day 9:00 PM', task='VC invite', tag_names=['VC Member']))
        m = self.msg(t0)
        await self.run_msg(m, 'set kardo reminder')
        self.assertEqual(self.store.load(), [])
