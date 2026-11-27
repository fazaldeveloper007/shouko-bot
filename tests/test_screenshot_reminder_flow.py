"""The 27 Nov 18:53-55 production screenshot, end to end through on_message.

The owner gave a COMPLETE daily reminder in one message (message text, daily
9:00 PM, VC Member role ping, a voice-channel mention inside the payload).
Live behavior: the bot re-asked for what was given, then lost the pending
state to a duplicate-history channel count, then the announcement path took
over (quiet, no role, one-shot "tomorrow"), and the "from today" correction
was answered with a generic repair line instead of editing the preview.

These tests pin the repaired behavior of the whole conversation, not single
functions: understanding -> clarification/correction -> authorized execution
-> verified saved outcome.
"""
import datetime as dt
import json
import types
import unittest
from unittest.mock import patch

import discord
import timed_messages as tm
from tests.test_offline_harness import HarnessBase, OWNER, OTHER, GUILD, CHAN

A = dt.datetime(2026, 11, 27, 18, 53, tzinfo=tm.IST)  # screenshot turn 1
DUE_9PM = dt.datetime(2026, 11, 27, 21, 0, tzinfo=tm.IST).timestamp()

SCREENSHOT_REQUEST = ('Set a reminder for daily purpose - message : invitation for Vc, '
                      'to join this <#555001> voice channel - time : daily 9:00 PM - ping : VC Member')


def role(rid, name, mentionable=True):
    return types.SimpleNamespace(id=rid, name=name, mentionable=mentionable,
                                 is_default=lambda: False)


class ScreenshotFlow(HarnessBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.vc_role = role(777, 'VC Member')
        self.guild.roles = [self.vc_role]
        self.guild.get_role = lambda rid: {777: self.vc_role}.get(rid)
        # tm.access verifies the live-looking client/guild/channel before save
        self.guild.unavailable = False
        self.guild.me = self.me
        self.guild.get_channel = lambda cid: {CHAN: self.channel, CHAN + 1: self.events}.get(cid)
        self.bot.client.is_ready = lambda: True
        self.bot.client.get_guild = lambda gid: self.guild if gid == GUILD else None
        self.channel.type = discord.ChannelType.text
        self.channel.guild = self.guild
        self.channel.permissions_for = lambda who: types.SimpleNamespace(
            view_channel=True, send_messages=True, mention_everyone=True)

    def at(self, author, content, when=A):
        m = self.msg(author, content)
        m.created_at = when
        return m

    def saved(self):
        return self.bot._schedule_store.load()

    async def test_turn1_complete_structured_request_saves_directly_no_model(self):
        # Exact screenshot wording: saved immediately, zero model calls, and
        # recurrence, payload channel mention and role audience all survive.
        self.script('Daily reminder kis time par set karu aur exact task kya hai, boss?')
        m = self.at(self.owner, f'Kristy {SCREENSHOT_REQUEST}')
        with patch('schedule_commands.time.time', return_value=A.timestamp()):
            await self.bot.on_message(m)
        self.brain.assert_not_called()  # no intent API call needed at all
        rows = self.saved()
        self.assertEqual(len(rows), 1)
        e = rows[0]
        self.assertEqual(e['status'], 'scheduled')
        self.assertEqual(e['recurrence'], dict(kind='daily', hour=21, minute=0,
                                               timezone='Asia/Kolkata'))
        self.assertEqual(e['due'], DUE_9PM)  # first occurrence TODAY, not tomorrow
        self.assertEqual(e['dest'], CHAN)    # posts here; #555001 is payload
        self.assertEqual(e['targets'], [OWNER])
        self.assertEqual(e['roles'], [777])
        self.assertIn('<#555001>', e['body'])          # voice channel kept in text
        self.assertIn('invitation for Vc', e['body'])
        self.assertTrue(e['body'].startswith('<@1> <@&777>\n'), e['body'])
        out = self.reply_text(m)
        self.assertIn('Repeating reminder saved', out)
        self.assertNotIn('kis time', out)  # never re-asks what was supplied

    async def test_turn1_unpingable_role_refused_honestly_no_save(self):
        self.vc_role.mentionable = False
        self.guild.me = self.me
        # harness channel grants mention_everyone; make it False so the role
        # genuinely cannot notify
        self.channel.permissions_for = lambda who: types.SimpleNamespace(
            view_channel=True, send_messages=True, mention_everyone=False)
        m = self.at(self.owner, f'Kristy {SCREENSHOT_REQUEST}')
        with patch('schedule_commands.time.time', return_value=A.timestamp()):
            await self.bot.on_message(m)
        self.assertEqual(self.saved(), [])
        self.assertIn('silent', self.reply_text(m))

    async def test_unknown_ping_name_refused_no_save(self):
        m = self.at(self.owner, 'Kristy set reminder - message : standup - time : daily 9:00 PM - ping : Ghost Role')
        with patch('schedule_commands.time.time', return_value=A.timestamp()):
            await self.bot.on_message(m)
        self.assertEqual(self.saved(), [])
        self.assertIn('nahi mila', self.reply_text(m))

    async def test_turn2_duplicate_history_not_double_counted_and_state_kept(self):
        # Model path (no structured form): the open clarification plus channel
        # history re-stating the original must not become "2 destinations",
        # and an input problem must not drop the pending request.
        request = 'Kristy mujhe daily 9:00 PM yaad dilana <#555001> join karne ka'
        self.script(json.dumps({'action': 'need_info', 'question': ' Kis time?'}))
        m1 = self.at(self.owner, request)
        with patch('schedule_commands.time.time', return_value=A.timestamp()):
            await self.bot.on_message(m1)
        self.assertEqual(self.saved(), [])
        self.assertIsNotNone(self.bot._schedule_commands.pending_item(m1))
        # the original is already in channel history (on_message records it),
        # so the follow-up sees it twice: pending original + history line
        self.script(json.dumps({'action': 'remind', 'when': 'every day 9:00 PM',
                                'task': 'join karne ka', 'tag_names': []}))
        m2 = self.at(self.owner, 'Kristy Upar padho', A + dt.timedelta(seconds=40))
        with patch('schedule_commands.time.time', return_value=(A + dt.timedelta(seconds=40)).timestamp()):
            await self.bot.on_message(m2)
        out2 = self.reply_text(m2)
        self.assertNotIn('Ek hi destination', out2)      # dedupe: one distinct channel
        self.assertIn('Kis channel mein bhejna hai', out2)
        self.assertEqual(self.saved(), [])
        # pending state SURVIVES the input problem (was popped before the fix)
        self.assertIsNotNone(self.bot._schedule_commands.pending_item(m2))
        # turn 3: "Current one" answers the destination question: #555001 is
        # payload, the reminder posts in the current channel, series intact
        m3 = self.at(self.owner, 'Kristy Current one', A + dt.timedelta(seconds=80))
        with patch('schedule_commands.time.time', return_value=(A + dt.timedelta(seconds=80)).timestamp()):
            await self.bot.on_message(m3)
        rows = self.saved()
        self.assertEqual(len(rows), 1)
        e = rows[0]
        self.assertEqual(e['dest'], CHAN)
        self.assertEqual(e['recurrence']['kind'], 'daily')
        self.assertEqual((e['recurrence']['hour'], e['recurrence']['minute']), (21, 0))
        self.assertIn('Repeating reminder saved', self.reply_text(m3))

    async def test_announce_path_refuses_reminder_shaped_requests(self):
        # competing-router guard: recurrence / role audience can only live in
        # the scheduler, so the announce marker path must refuse them instead
        # of silently degrading (daily -> one-shot, role -> quiet post).
        # on_message level: recurrence wording is refused with the redirect
        self.script('[[do:announce quiet | daily 9 PM | invitation for VC]]')
        m = self.at(self.owner, 'Kristy daily 9 PM ka announcement daal do VC joining ka')
        await self.bot.on_message(m)
        self.assertIn('Repeating', self.reply_text(m))
        self.assertFalse(self.bot._actions.pending)
        self.channel.send.assert_not_called()
        # role audience refused at the marker-execution boundary (on_message
        # phrasings for these collide with the direct-ping and roster routers,
        # which is itself correct routing; the guard sits behind them)
        acts = self.bot._actions
        for text in ('Kristy VC Member ping karke announcement daal do',
                     'Kristy VC Member ko ping karke ek announcement bana do',
                     'Kristy announcement daal do, ping : VC Member'):
            out = await acts.maybe_execute(self.at(self.owner, text),
                                           '[[do:announce quiet | invitation for VC]]')
            self.assertIn('role ka ping nahi', out, text)
            self.assertFalse(acts.pending, text)
        # a plain announcement request still previews fine (tag karke is not a role)
        self.script('[[do:announce everyone | Scrim Saturday 8 baje, sab aa jao]]')
        m = self.at(self.owner, 'Kristy is channel mein announcement daal do everyone tag karke scrim ke baare mein')
        await self.bot.on_message(m)
        self.assertIn('preview', self.reply_text(m))
        self.assertEqual(len(self.bot._actions.pending), 1)

    async def test_announce_blocked_while_reminder_clarification_open(self):
        acts = self.bot._actions
        acts.reminder_pending = lambda message: True
        try:
            out = await acts.maybe_execute(self.at(self.owner, 'Kristy announce karo'),
                                           '[[do:announce quiet | text]]')
            self.assertIn('reminder', out)
            self.assertFalse(acts.pending)
        finally:
            acts.reminder_pending = None

    async def test_scheduled_preview_date_correction_updates_same_preview(self):
        # preview exists (scheduled for tomorrow 9 PM); "Not tommorow from
        # today" edits THAT preview's date - no new flow, nothing posted.
        m1 = self.at(self.owner, 'Kristy announce schedule tomorrow 9 PM | quiet | general | invitation for VC')
        with patch('actions.time.time', return_value=A.timestamp()):
            await self.bot.on_message(m1)
        out1 = self.reply_text(m1)
        self.assertIn('preview', out1)
        self.assertIn('posting time:', out1)
        self.assertEqual(len(self.bot._actions.pending), 1)
        ident = next(iter(self.bot._actions.pending))
        self.channel.send.assert_not_called()
        m2 = self.at(self.owner, 'Kristy Not tommorow from today', A + dt.timedelta(minutes=2))
        with patch('actions.time.time', return_value=(A + dt.timedelta(minutes=2)).timestamp()):
            await self.bot.on_message(m2)
        out2 = self.reply_text(m2)
        self.assertIn('posting time update kiya', out2)
        self.assertIn('27 November 2026, 09:00 PM', out2)
        self.assertNotIn('28 November', out2)
        self.assertEqual(self.bot._actions.pending[ident]['due'], DUE_9PM)  # today, not tomorrow
        self.assertEqual(len(self.bot._actions.pending), 1)  # same preview, not a new one
        self.channel.send.assert_not_called()
        # and the corrected preview still confirms into a real schedule
        m3 = self.at(self.owner, 'Kristy announce confirm ' + ident, A + dt.timedelta(minutes=3))
        with patch('actions.time.time', return_value=(A + dt.timedelta(minutes=3)).timestamp()):
            await self.bot.on_message(m3)
        self.assertIn('Announcement scheduled', self.reply_text(m3))

    async def test_draft_embedded_date_correction_swaps_date_word(self):
        # the screenshot state itself: quiet preview, posting "now", with the
        # date buried in the draft text ("tomorrow 9 PM | invitation...").
        self.script('[[do:announce quiet | tomorrow 9 PM invitation for Vc, to join this Gaming voice channel]]')
        m1 = self.at(self.owner, 'Kristy invitation ka announcement daal do')
        with patch('actions.time.time', return_value=A.timestamp()):
            await self.bot.on_message(m1)
        self.assertIn('tomorrow 9 PM', self.reply_text(m1))
        ident = next(iter(self.bot._actions.pending))
        m2 = self.at(self.owner, 'Kristy Not tommorow from today', A + dt.timedelta(minutes=2))
        with patch('actions.time.time', return_value=(A + dt.timedelta(minutes=2)).timestamp()):
            await self.bot.on_message(m2)
        out2 = self.reply_text(m2)
        self.assertIn('posting time update kiya', out2)
        self.assertIn('today 9 PM', out2)
        self.assertNotIn('tomorrow 9 PM', out2)
        self.assertEqual(self.bot._actions.pending[ident]['due'], DUE_9PM)
        self.channel.send.assert_not_called()

    async def test_correction_without_open_preview_is_normal_chat(self):
        self.script('theek hai, noted!')
        m = self.at(self.owner, 'Kristy kal cricket match hai... wait aaj hi hai')
        await self.bot.on_message(m)
        self.assertEqual(self.reply_text(m), 'theek hai, noted!')  # not consumed
        self.assertFalse(self.bot._actions.pending)


if __name__ == '__main__':
    unittest.main()
