"""Model-requested, code-executed actions - the bounded agent loop.

The brain may START its reply with ONE marker asking the runtime to act:

  [[do:music status]]     now playing + queue, composed from live player state
  [[do:music pause]]      [[do:music resume]] [[do:music skip]] [[do:music stop]]
  [[do:reminder list]]    the member's own saved schedules (ids + due times)
  [[do:announce | draft]] owner-only: preview an @everyone announcement for this
                          channel; it posts only after the owner's single-use
                          confirm, then reports the real message link

Code executes the marker and writes the result text itself, so a reply can
never claim an action that did not happen. No marker: normal chat, zero extra
API calls. Announce previews are single-use, expire, and are bound to the
requesting owner and the channel they were made in; the owner approves with a
plain "confirm" (or "cancel") in the same channel - their latest pending
preview there. Several pending previews at once are never guessed at: the
owner replies to the exact preview message. The preview text itself never
pings (the normal reply path allows no everyone/role mentions), and the
member/role mention syntax inside the model's draft is stripped before it is
shown or posted - the only ping in the posted announcement is the code-added
@everyone the preview declared.
"""
import datetime as dt
import logging
import re
import secrets
import time

import discord

import agent_core
from music.player import MusicError
from music.ui import clean
import timed_messages as tm
import os
import server_builder

log = logging.getLogger('kristy.actions')

DO_RE = re.compile(r"^\s*\[\[\s*do\s*:\s*([a-z]+(?:\s+[a-z]+){0,3})(?:\s+#([a-z0-9_-]{1,32}))?\s*(?:\|\s*([^\]]{5,600}?))?\s*\]\]", re.I)
SCHEDULE_RE = re.compile(r"^\s*\[\[do:announce (everyone|quiet)(?: #([a-z0-9_-]{1,32}))? \| ([^|\]]{3,80}) \| ([^\]]{5,600})\]\]\s*$", re.I)
TIME_RE = re.compile(r'(?:aaj|today|kal|tomorrow|\d{4}-\d{2}-\d{2})\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\d+\s*(?:minutes?|min|hours?|ghante|ghanta)\s+baad',re.I)
CONFIRM_RE = re.compile(r"announce\s+(confirm|cancel)\s+([a-f0-9]{10})\s*$", re.I)
PLAIN_RE = re.compile(r"(?:announce\s+)?(confirm|cancel)\s*$", re.I)

CAPABILITY = (
    "\n\nACTIONS YOU CAN ACTUALLY DO: When a member clearly asks you to DO one "
    "of these, reply with ONLY the matching marker on its own first line - "
    "the runtime performs it and writes the real result, which the member "
    "sees instead of your marker:\n"
    "[[do:music status]] - what is playing now\n"
    "[[do:music queue]] - the full waiting queue\n"
    "[[do:music pause]] / [[do:music resume]] / [[do:music skip]] / [[do:music stop]]\n"
    "[[do:reminder list]] - the member's own saved reminders\n"
    "[[do:announce everyone | full announcement text here]] - draft an "
    "announcement that pings everyone in this channel\n"
    "[[do:announce quiet | full announcement text here]] - draft an "
    "announcement with NO ping\n"
    "If they name a DIFFERENT target channel, add it after the scope word, "
    "e.g. [[do:announce everyone #events | text]]; leave it out for the "
    "channel they are in.\n"
    "Announce only when the server owner clearly asks for one: write the "
    "complete polished announcement after the | (no @mentions in it), choose "
    "everyone vs quiet from what they asked, and leave the scope word out when "
    "they did not say - the runtime will ask them. The owner reviews the exact "
    "preview before anything is posted.\n"
    "Preparing IS announcing here: when they say tayyar karo / draft karo / "
    "bana do about an announcement for a channel, use the marker - the "
    "runtime preview IS the prepared draft they review. Reply with "
    "announcement text as plain chat (no marker) ONLY when they clearly want "
    "to copy the text and post it themselves.\n"
    "For a selected-time announcement use [[do:announce everyone #events | tomorrow 9 PM | full text]] "
    "or quiet instead of everyone. Copy the exact date/time phrase from the CURRENT request, "
    "not an event time merely described in the announcement. Supported: today/tomorrow 9 PM, "
    "YYYY-MM-DD HH:MM, or 10 minutes baad. Never turn a scheduled post into an immediate one. "
    "If a requested posting time is incomplete, ask for it instead. The runtime previews the "
    "exact IST time and saves only after requester confirm. No API text rewrite at send time. "
    "Rules: at most one marker per reply, only when the request is clear - "
    "ask a short question instead when it is not. NEVER claim you did one of "
    "these things in plain text; the marker is the only way, and the runtime "
    "reports the true outcome. For everything else (chat, questions, jokes) "
    "answer normally with no marker."
)
if os.getenv('KRISTY_SERVER_BUILDER', '1') != '0':
    CAPABILITY += server_builder.CAPABILITY


class Actions:
    PREVIEW_TTL = 300  # seconds

    def __init__(self, client, owner_id, schedule_store, ledger=None):
        self.client = client
        self.owner_id = (owner_id or '').strip()
        self.store = schedule_store
        self.pending = {}
        self.clarify_stash = {}  # (author, channel) -> interrupted draft+scope
        self.last_preview = None  # (channel, ident) awaiting its sent-message id
        self.ledger = ledger
        self.executor = agent_core.Executor(ledger) if ledger is not None else None
        self.reminder_pending = None  # bot.py wires: open reminder clarification probe
        self.builder = server_builder.Builder(client, ledger=ledger) if os.getenv('KRISTY_SERVER_BUILDER', '1') != '0' else None

    def _ledger_stash_preview(self, ident, entry):
        if self.ledger is None:
            return
        try:
            self.ledger.create('announce_preview', agent_core.PENDING, ident=ident,
                               guild=entry['guild'], channel=entry['channel'], author=entry['author'],
                               payload={'entry': {k: v for k, v in entry.items() if k != 'expires'}},
                               expires=agent_core.wall_from_monotonic(entry['expires']))
        except Exception:
            log.warning('ledger preview stash failed', exc_info=True)

    def _ledger_settle_preview(self, ident, status, result=None):
        if self.ledger is None:
            return
        try:
            self.ledger.update(ident, status=status, result=result)
        except Exception:
            log.warning('ledger preview settle failed', exc_info=True)

    def restore_pending(self):
        """Reload unexpired announcement previews (and server plans) after a
        restart, so a pending confirm/cancel still resolves."""
        if self.ledger is None:
            return 0
        restored = 0
        for row in self.ledger.pending('announce_preview'):
            try:
                entry = dict(row['payload'].get('entry') or {})
                entry['expires'] = time.monotonic() + max(0.0, row['expires'] - time.time())
                self.pending[row['id']] = entry
                restored += 1
            except Exception:
                log.warning('ledger preview restore skipped a row', exc_info=True)
        if self.builder is not None:
            restored += self.builder.restore_pending()
        return restored

    def layout_note(self, channel_id):
        return self.builder.layout_note(channel_id) if self.builder else ''

    def prime_layout(self, message, content):
        if self.builder:
            self.builder.prime_layout(message, content)

    def is_owner(self, message):
        return bool(self.owner_id) and str(message.author.id) == self.owner_id

    async def handle(self, message, text):
        """Deterministic announce confirm/cancel. True when the message is consumed.

        Two forms: the explicit code form (announce confirm <code>) and plain
        \"confirm\"/\"cancel\" (optionally \"announce confirm\"), which acts on the
        sender's own pending preview in this channel. Several pending previews
        are never guessed at: the owner replies to the exact preview message.
        Plain words with no pending preview are NOT consumed - ordinary chat
        must not be hijacked.
        """
        t = (text or '').strip()
        if self.builder and await self.builder.handle(message, t):
            return True
        if await self._maybe_correct_preview(message, t):
            return True
        explicit=re.fullmatch(r'announce schedule ([^|]{3,80})\|\s*(everyone|quiet)\s*\|\s*#?([a-z0-9_-]{1,32})\s*\|\s*(.{5,600})',t,re.I|re.S)
        if explicit:
            blocked=self._announce_blocked(message)
            if blocked:
                await message.reply(blocked,mention_author=False);return True
            when,scope,channel,draft=explicit.groups()
            try:due=self._announcement_due(message,when.strip(),t)
            except ValueError as exc:
                await message.reply(str(exc),mention_author=False);return True
            out=self._announce_preview(message,draft,scope.lower()=='everyone',channel,due)
            await message.reply(out,mention_author=False,allowed_mentions=discord.AllowedMentions.none())
            return True
        m = CONFIRM_RE.fullmatch(t)
        if m:
            verb, ident = m.group(1).lower(), m.group(2).lower()
            entry = self.pending.get(ident)
            if entry is None:
                await message.reply('Ye announcement preview nahi mila (expire ho gaya ya already done). Naya preview banao.', mention_author=False)
                return True
            return await self._finish(message, verb, ident, entry)
        m = PLAIN_RE.fullmatch(t)
        if not m:
            return False
        now = time.monotonic()
        for stale in [k for k, e in self.pending.items() if now > e['expires']]:
            self.pending.pop(stale, None)
            self._ledger_settle_preview(stale, agent_core.EXPIRED)
        mine = [(k, e) for k, e in self.pending.items()
                if e['author'] == message.author.id and e['channel'] == message.channel.id]
        if not mine:
            return False  # ordinary chat confirm/cancel - nothing of ours pending
        verb = m.group(1).lower()
        if len(mine) > 1:
            ref = getattr(getattr(message, 'reference', None), 'message_id', None)
            bound = [k for k, e in mine if e.get('preview_msg') is not None
                     and e['preview_msg'] == ref]
            if len(bound) != 1:
                await message.reply('Ek se zyada announcement preview pending hain; jis wale ko confirm ya cancel karna hai, us preview message par reply karke bhejo.', mention_author=False)
                return True
            ident = bound[0]
        else:
            ident = mine[0][0]
        return await self._finish(message, verb, ident, self.pending[ident])

    _DATE_TODAY = re.compile(r'\b(?:today|aaj)\b', re.I)
    _DATE_TOMORROW = re.compile(r'\b(?:tomorrow|tommorow|tomorow|kal)\b', re.I)
    _NEG_TODAY = re.compile(r'\b(?:not|nahi|nhi|mat|no)\s+(?:for\s+)?(?:today|aaj)\b', re.I)
    _NEG_TOMORROW = re.compile(r'\b(?:not|nahi|nhi|mat|no)\s+(?:for\s+)?(?:tomorrow|tommorow|tomorow|kal)\b', re.I)
    _CLOCK = re.compile(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', re.I)

    def _correction_date(self, t):
        """Which date the owner means in a correction like 'not tomorrow, from
        today': the negated date word loses, the surviving one wins."""
        neg_today = bool(self._NEG_TODAY.search(t)); neg_tom = bool(self._NEG_TOMORROW.search(t))
        want_today = bool(self._DATE_TODAY.search(t)) and not neg_today
        want_tom = bool(self._DATE_TOMORROW.search(t)) and not neg_tom
        if neg_tom and want_today:return 'aaj'
        if neg_today and want_tom:return 'kal'
        if want_today != want_tom:return 'aaj' if want_today else 'kal'
        return None

    def _preview_text(self, entry, channel_name):
        due = entry.get('due')
        draft = entry['text']
        if entry['ping'] and draft.startswith('@everyone\n'):
            draft = draft[len('@everyone\n'):]
        return ('announcement preview (abhi post NAHI hua):\n'
                f'channel: #{channel_name}\n'
                f'ping: {"everyone" if entry["ping"] else "none (quiet post)"}\n'
                + (f'posting time: {tm.shown(due)}; more than 2 minutes late = skip\n' if due is not None else 'posting time: now, after confirm\n') +
                f'---\n{draft}\n---\n'
                'bhejne ke liye: confirm\n'
                'cancel ke liye: cancel\n'
                'preview 5 minute mein expire ho jayega.')

    async def _maybe_correct_preview(self, message, t):
        """A date/time correction to the owner's own open preview edits THAT
        preview in place ('not tomorrow, from today' moves the same draft to
        today) instead of falling through to chat, where the model would
        start an unrelated new flow. Not consumed when no correction is
        meant; the pending preview itself is never lost."""
        if not re.search(r'\b(?:today|aaj|tomorrow|tommorow|tomorow|kal)\b', t or '', re.I):
            return False
        now = time.monotonic()
        mine = [(k, e) for k, e in self.pending.items()
                if e['author'] == message.author.id and e['channel'] == message.channel.id]
        if len(mine) != 1:
            return False
        ident, entry = mine[0]
        if now > entry['expires']:
            return False
        anchor = getattr(message, 'created_at', None)
        if not isinstance(anchor, dt.datetime) or anchor.tzinfo is None:
            return False
        date_word = self._correction_date(t)
        clock = None
        m = self._CLOCK.search(t)
        if m:
            clock = (int(m[1]), int(m[2] or 0), m[3].lower())
        if date_word is None and clock is None:
            return False
        old_due = entry.get('due')
        if clock is None:
            m2 = self._CLOCK.search(entry.get('text', '') or '')
            if m2:
                clock = (int(m2[1]), int(m2[2] or 0), m2[3].lower())
        local_anchor = anchor.astimezone(tm.IST)
        if date_word in ('aaj', 'today'):
            date = local_anchor.date()
        elif date_word in ('kal', 'tomorrow'):
            date = local_anchor.date() + dt.timedelta(days=1)
        elif old_due is not None:
            date = dt.datetime.fromtimestamp(old_due, tm.IST).date()
        else:
            date = local_anchor.date()
        if clock is not None:
            hour = clock[0] % 12 + (12 if clock[2] == 'pm' else 0); minute = clock[1]
        elif old_due is not None:
            old_local = dt.datetime.fromtimestamp(old_due, tm.IST)
            hour, minute = old_local.hour, old_local.minute
        else:
            await message.reply('Naya posting time samajh nahi aaya (AM/PM ke saath likho); preview wahi pending hai, kuch change nahi hua.', mention_author=False)
            return True
        new_due = dt.datetime.combine(date, dt.time(hour, minute), tm.IST).timestamp()
        if new_due <= time.time():
            await message.reply('Woh time nikal gaya; preview wahi pending hai, kuch change nahi hua.', mention_author=False)
            return True
        entry['due'] = new_due
        entry['expires'] = time.monotonic() + self.PREVIEW_TTL
        # keep the announcement text's own date word honest with the new date
        if date_word in ('aaj', 'today'):
            updated, n = self._DATE_TOMORROW.subn('today', entry['text'], count=1)
        else:
            updated, n = self._DATE_TODAY.subn('tomorrow', entry['text'], count=1)
        if n:
            entry['text'] = updated
        self._ledger_stash_preview(ident, entry)
        self.last_preview = (message.channel.id, ident)
        target = self.client.get_channel(entry.get('target', entry['channel']))
        await message.reply('Theek hai, posting time update kiya:\n' +
                            self._preview_text(entry, getattr(target, 'name', '?')),
                            mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        return True

    def _announce_blocked(self, message):
        """An announcement must not take over a reminder request: the announce
        path has no series and no role audience, so a daily or role-ping
        request landing here would silently lose both. Returns the refusal
        line, or None when the request is a plain announcement."""
        content = getattr(message, 'content', '') or ''
        if self.reminder_pending is not None:
            try:
                if self.reminder_pending(message):
                    return ('Tumhara reminder ka clarification abhi khula hai; pehle wahi poora karo. '
                            'Announcement preview nahi banaya.')
            except Exception:
                log.warning('reminder_pending probe failed', exc_info=True)
        if re.search(r'\b(?:every\s+day|daily|weekly|monthly|har\s+din)\b|\bevery\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', content, re.I):
            return ('Repeating (daily/weekly/monthly) post announcement se nahi banta; woh reminder ka kaam hai. '
                    'Jaise: Kristy remind ... every day 9 PM. Kuch preview/save nahi hua.')
        role_refusal = ('Announcement sirf @everyone ya quiet hota hai, kisi role ka ping nahi. '
                        'Role ping wala scheduled message reminder/schedule se banao. Kuch preview/save nahi hua.')
        if re.search(r'<@&\d+>', content):
            return role_refusal
        names = {getattr(r, 'name', '').casefold() for r in (getattr(message.guild, 'roles', None) or [])
                 if not getattr(r, 'is_default', lambda: False)()}
        if names:
            spans = []
            for m in re.finditer(r'\b(?:tag(?:ging|ged)?|ping(?:ing|ed)?)\b\s*[:,.\-]?\s*@?([\w.\-]+(?:\s+[\w.\-]+){0,4})', content, re.I):
                spans.append(m[1])
            for m in re.finditer(r'(?<![\w.])@?([\w.\-]+(?:\s+[\w.\-]+){0,4})\s+(?:ko\s+)?(?:tag|ping)\b', content, re.I):
                spans.append(m[1])
            for span in spans:
                words = span.split()
                for i in range(len(words)):
                    for n in range(len(words) - i, 0, -1):
                        if ' '.join(words[i:i + n]).casefold() in names:
                            return role_refusal
        return None

    def note_preview_sent(self, channel_id, message_id):
        """Bind the just-sent preview's message id for reply-based disambiguation."""
        last = self.last_preview
        self.last_preview = None
        if last is None or message_id is None:
            return
        chan, ident = last
        if chan == channel_id and ident in self.pending:
            self.pending[ident]['preview_msg'] = message_id

    async def _finish(self, message, verb, ident, entry):
        """Single-use confirm/cancel execution shared by both invocation forms."""
        if entry['author'] != message.author.id:
            await message.reply('Ye preview kisi aur ka hai; sirf jisne banaya wahi confirm ya cancel kar sakta hai.', mention_author=False)
            return True
        if message.channel.id != entry['channel']:
            await message.reply('Confirm wahi channel mein karo jahan preview bana tha.', mention_author=False)
            return True
        if time.monotonic() > entry['expires']:
            self.pending.pop(ident, None)
            self._ledger_settle_preview(ident, agent_core.EXPIRED)
            await message.reply('Ye preview expire ho gaya; naya preview banao.', mention_author=False)
            return True
        self.pending.pop(ident, None)  # single-use for both verbs
        if verb == 'cancel':
            self._ledger_settle_preview(ident, agent_core.CANCELLED)
            await message.reply('Announcement cancel kar diya; kuch post nahi hua.', mention_author=False)
            return True
        channel = self.client.get_channel(entry.get('target', entry['channel']))
        if channel is None:
            self._ledger_settle_preview(ident, agent_core.FAILED, result='channel_inaccessible')
            await message.reply('Channel ab accessible nahi hai; announcement post nahi hua.', mention_author=False)
            return True
        if entry.get('due') is not None:
            try:
                if not self.is_owner(message):raise ValueError('Owner authority changed.')
                now=time.time()
                if entry['due']-now<1:raise ValueError('Selected time nikal gaya; announcement save nahi hua. Naya preview banao.')
                candidate=dict(guild=entry['guild'],author=entry['author'],source=entry['channel'],dest=entry['target'],
                    targets=[],body=entry['text'],due=entry['due'],kind='announcement',ping=entry['ping'])
                tm.access(self.client,candidate)
                if self.executor is not None:
                    # Execution boundary: DONE only when the read-back verifies
                    # the scheduled job actually exists.
                    outcome = await self.executor.run_pending(ident,
                        handler=lambda: self.store.create(**candidate, now=now, armed=True),
                        verify=lambda s: agent_core.verify_saved(self.store, s['id']),
                        result_of=lambda s: f"scheduled:{s['id']}")
                    if not outcome.ok:
                        raise OSError('announcement save verify nahi hua')
                    saved = outcome.result
                else:
                    saved=self.store.create(**candidate,now=now,armed=True)
                    # Execution truth: claim "scheduled" only after read-back.
                    if agent_core.verify_saved(self.store,saved['id']) is None:
                        raise OSError('announcement save verify nahi hua')
                    self._ledger_settle_preview(ident,agent_core.DONE,result=f"scheduled:{saved['id']}")
            except (ValueError,OSError,TypeError):
                self._ledger_settle_preview(ident,agent_core.FAILED)
                await message.reply('Announcement schedule save nahi hua; time, permissions ya storage check karo. Kuch post nahi hua.',mention_author=False)
                return True
            await message.reply(f"Announcement scheduled: {tm.shown(saved['due'])}. ID: {saved['id']}. Cancel: Kristy schedule cancel {saved['id']}. 2 minute se zyada late hua toh skip hoga.",mention_author=False)
            return True
        async def _send():
            return await channel.send(entry['text'], allowed_mentions=discord.AllowedMentions(everyone=entry['ping'], users=False, roles=False))
        if self.executor is not None:
            outcome = await self.executor.run_pending(ident, _send)
            if not outcome.ok:
                # The error may have happened after Discord accepted the message.
                # Never claim a definite failure and never auto-retry a duplicate.
                log.warning('announce send outcome unknown: %s', type(outcome.error).__name__ if outcome.error else 'verify_failed')
                self._ledger_settle_preview(ident, agent_core.FAILED, result='send_outcome_unknown')
                await message.reply('Send ka result confirm nahi hua - ho sakta hai post ho gaya ho. Channel khud check karo; main apne aap dobara NAHI bhejungi. Naya preview chahiye toh phir se bolo.', mention_author=False)
                return True
            sent = outcome.result
            self._ledger_settle_preview(ident, agent_core.DONE, result='posted')
            await message.reply(f'Announcement post ho gaya ✅ {sent.jump_url}', mention_author=False)
            return True
        try:
            sent = await _send()
        except Exception as exc:
            # The error may have happened after Discord accepted the message.
            # Never claim a definite failure and never auto-retry a duplicate.
            log.warning('announce send outcome unknown: %s', type(exc).__name__)
            self._ledger_settle_preview(ident,agent_core.FAILED,result='send_outcome_unknown')
            await message.reply('Send ka result confirm nahi hua - ho sakta hai post ho gaya ho. Channel khud check karo; main apne aap dobara NAHI bhejungi. Naya preview chahiye toh phir se bolo.', mention_author=False)
            return True
        self._ledger_settle_preview(ident,agent_core.DONE,result='posted')
        await message.reply(f'Announcement post ho gaya ✅ {sent.jump_url}', mention_author=False)
        return True

    async def maybe_execute(self, message, reply):
        """Execute a leading [[do:...]] marker; return the final reply text."""
        if self.builder:
            plan = self.builder.plan(message, reply)
            if plan is not None:
                return plan
        scheduled=SCHEDULE_RE.fullmatch(reply or '')
        if scheduled:
            blocked=self._announce_blocked(message)
            if blocked:return blocked
            scope,channel,when,draft=scheduled.groups()
            try:due=self._announcement_due(message,when,getattr(message,'content',''))
            except ValueError as exc:return str(exc)
            return self._announce_preview(message,draft,scope.lower()=='everyone',channel,due)
        m = DO_RE.match(reply or '')
        if not m:
            return reply
        cmd, chan_name = m.group(1).lower(), m.group(2)
        draft = (m.group(3) or '').strip()
        log.info('action requested: %s (channel=%s)', cmd, message.channel.id)
        try:
            if cmd == 'music status':
                return await self._music_status(message)
            if cmd == 'music queue':
                return await self._music_queue(message)
            if cmd == 'music pause':
                return await self._music_pause(message, True)
            if cmd == 'music resume':
                return await self._music_pause(message, False)
            if cmd == 'music skip':
                return await self._music_skip(message)
            if cmd == 'music stop':
                return await self._music_stop(message)
            if cmd == 'reminder list':
                return self._reminder_list(message)
            if cmd.startswith('announce'):
                if not self.is_owner(message):return 'announcement sirf server owner kara sakta hai.'
                blocked=self._announce_blocked(message)
                if blocked:return blocked
                # An AI marker may not silently drop a requested posting time.
                if re.search(r'\b(?:schedule|scheduled|at|baje|baad|tomorrow|kal|today|aaj)\b|\d{1,2}\s*(?:am|pm)\b',getattr(message,'content',''),re.I):
                    return 'Posting time clear karo: Kristy announce schedule tomorrow 9 PM | quiet | #channel | announcement text. Event time aur posting time alag hain; abhi kuch save/post nahi hua.'
                key = (message.author.id, message.channel.id)
                stash = self.clarify_stash.pop(key, None)
                if stash and time.monotonic() > stash['expires']:
                    stash = None
                saved_due=stash.get('due') if stash else None
                if stash:
                    if not draft:
                        draft = stash.get('draft', '')
                    if cmd == 'announce' and stash.get('ping') is not None:
                        cmd = 'announce everyone' if stash['ping'] else 'announce quiet'
                if cmd == 'announce everyone':
                    return self._announce_preview(message, draft, ping=True, chan_name=chan_name, due=saved_due)
                if cmd == 'announce quiet':
                    return self._announce_preview(message, draft, ping=False, chan_name=chan_name, due=saved_due)
                if cmd == 'announce':
                    self.clarify_stash[key] = {'draft': draft, 'ping': None,
                                               'expires': time.monotonic() + self.PREVIEW_TTL}
                    return ('everyone tag ke saath bhejna hai ya bina ping ke (quiet)? '
                            'scope bata do, phir exact preview dungi.')
        except MusicError as exc:
            return str(exc)  # user-facing music rules (join VC, use VC chat)
        except Exception:
            log.warning('action failed: %s', cmd, exc_info=True)
            return 'wo kaam abhi complete nahi hua (internal error); kuch change nahi hua.'
        log.warning('unknown action marker ignored: %s', cmd)
        return 'samjhi nahi kya karna tha; thoda clearly bata do 🥺'

    def _player(self, message):
        music = getattr(self.client, 'music', None)
        if music is None:
            raise MusicError('Music abhi disabled hai.')
        manager = music.manager
        vc = manager.caller_channel(message.author, message.channel)
        return manager.players.get(vc.guild.id)

    async def _music_status(self, message):
        player = self._player(message)
        if player is None or player.closed or (player.current is None and not player.queue):
            return 'abhi kuch nahi baj raha; queue bhi khaali hai.'
        lines = []
        if player.current is not None:
            state = 'paused' if player.paused else 'playing'
            lines.append(f'abhi chal raha hai: {clean(player.current.title, 90)} ({state})')
        else:
            lines.append('abhi koi track nahi chal raha.')
        queued = list(player.queue)
        if queued:
            shown = '; '.join(clean(t.title, 60) for t in queued[:5])
            lines.append(f'queue mein {len(queued)}: {shown}' + (' ...' if len(queued) > 5 else ''))
        else:
            lines.append('queue khaali hai.')
        if player.autoplay:
            lines.append('autoplay on hai: queue khatam hone par related gaane chalenge.')
        return '\n'.join(lines)

    async def _music_pause(self, message, paused):
        player = self._player(message)
        if player is None or player.closed or player.current is None:
            return 'abhi koi track nahi chal raha.'
        try:
            await player.pause(paused)
        except MusicError as exc:
            return f'nahi ho paya: {exc}'
        # Confirm from actual state, not from the request having been made.
        if player.paused != paused:
            return 'command gayi par state confirm nahi hui; panel ka actual status dekho.'
        return 'pause kar diya ⏸️' if paused else 'resume kar diya ▶️'

    async def _music_skip(self, message):
        player = self._player(message)
        if player is None or player.closed or player.current is None:
            return 'abhi koi track nahi chal raha; skip karne ko kuch nahi hai.'
        next_title = clean(player.queue[0].title, 60) if player.queue else None
        await player.interrupt()
        # The skip itself completed; the next track starts asynchronously and
        # can still fail on its own, so name it as upcoming, never as playing.
        if next_title:
            return f'skip kar diya ⏭️ agla shuru ho raha hai: {next_title} (start hua ya nahi, panel par confirm hoga)'
        return 'skip kar diya ⏭️ queue mein agla kuch nahi tha.'

    async def _music_stop(self, message):
        player = self._player(message)
        if player is None or player.closed or (player.current is None and not player.queue):
            return 'pehle se sab band hai; VC mein hoon, gaana bolo toh baja dungi.'
        await player.interrupt(clear=True)
        if player.queue or player.current is not None:
            return 'stop command gayi par queue/state confirm nahi hui; panel check karo.'
        return 'music band kar diya ⏹️ queue clear; VC mein hi rahi.'

    def _reminder_list(self, message):
        if message.guild is None:
            return 'DM mein server schedules nahi hote.'
        try:
            entries = self.store.load()
        except (OSError, ValueError, TypeError):
            return 'schedule storage abhi padh nahi payi; kuch confirm nahi kar sakti.'
        rows = []
        for e in entries:
            if e.get('guild') != message.guild.id or e.get('author') != message.author.id:
                continue
            # store schema: status is preview/scheduled/...; the pending vs
            # confirmed distinction lives on the reason field
            if e.get('status') not in ('preview', 'scheduled') \
                    or (not e.get('recurrence') and e.get('reason') not in ('awaiting_confirmation', 'confirmed')):
                continue
            rows.append(f"{e['id']} | {tm.shown(e['due'])} | {e['reason']}" + (f" | {tm.recurrence_label(e)} | last={e.get('last_status','none')}" if e.get('recurrence') else ''))
        if not rows:
            return 'tumhara koi pending reminder ya schedule nahi hai.'
        return ('tumhare saved schedules:\n' + '\n'.join(rows[:8])
                + (f'\n...aur {len(rows) - 8}' if len(rows) > 8 else ''))

    async def _music_queue(self, message):
        player = self._player(message)
        if player is None or player.closed or not player.queue:
            return 'queue khaali hai.'
        queued = list(player.queue)
        lines = [f'queue mein {len(queued)} track:']
        lines += [f'{i+1}. {clean(t.title, 80)}' for i, t in enumerate(queued[:10])]
        if len(queued) > 10:
            lines.append(f'...aur {len(queued) - 10}')
        return '\n'.join(lines)

    def _resolve_channel(self, message, chan_name):
        """Resolve a model-named target channel inside this guild.

        Returns (channel, clarification). Unknown or ambiguous names never
        fall back to the current channel silently - they get a question.
        """
        if not chan_name:
            return message.channel, None
        pool = list(getattr(message.guild, 'text_channels', None)
                    or getattr(message.guild, 'channels', []) or [])
        if chan_name.isdigit():
            # A native Discord channel mention arrives as its snowflake; look
            # the id up inside this guild's validated text channels first.
            ch = {getattr(c, 'id', None): c for c in pool}.get(int(chan_name))
            if ch is None:
                return None, ('wo channel is server mein nahi mila; sahi channel '
                              'bata do, ya bina naam ke bolo toh isi channel mein '
                              'preview bana dungi.')
            return ch, None
        want = chan_name.lower().replace('_', '-')
        named = [c for c in pool if getattr(c, 'name', '').lower() == want]
        if getattr(message.channel, 'name', '').lower() == want:
            return message.channel, None
        if not named:
            return None, (f'#{want} naam ka channel is server mein nahi mila; '
                          'sahi channel naam bata do, ya bina naam ke bolo toh '
                          'isi channel mein preview bana dungi.')
        if len(named) > 1:
            return None, (f'#{want} naam ke kai channels hain; kaunsa wala? '
                          'thoda specific bata do.')
        return named[0], None

    def _announcement_due(self,message,when,human):
        anchor=getattr(message,'created_at',None)
        if not isinstance(anchor,dt.datetime) or anchor.tzinfo is None:
            raise ValueError('Original request ka time verify nahi hua; kuch save nahi hua.')
        normalize=lambda v:' '.join(v.lower().split())
        phrases=[m.group(0) for m in TIME_RE.finditer(human)]
        if normalize(when) not in [normalize(v) for v in phrases]:
            raise ValueError('Posting date/time original request se verify nahi hua. Exact date aur AM/PM do; kuch save nahi hua.')
        due=tm.parse_time(when,anchor)
        if due<=time.time():raise ValueError('Posting time nikal gaya; kuch save nahi hua.')
        return due

    def _announce_preview(self, message, draft, ping, chan_name=None, due=None):
        if message.guild is None:
            return 'announcement server channel mein banti hai, DM mein nahi.'
        if not self.owner_id:
            return 'announce abhi configured nahi hai (OWNER_ID set nahi).'
        if not self.is_owner(message):
            return 'announcement sirf server owner kara sakta hai.'
        target, clarify = self._resolve_channel(message, chan_name)
        if clarify:
            # Keep the interrupted draft+scope: the owner's follow-up (e.g.
            # \"isi channel mein dedo\") continues the SAME announcement.
            self.clarify_stash[(message.author.id, message.channel.id)] = {
                'draft': draft, 'ping': ping, 'due': due,
                'expires': time.monotonic() + self.PREVIEW_TTL}
            return clarify
        if not draft:
            return 'kis baare mein announce karna hai? thoda context do, phir draft bana dungi.'
        me = message.guild.me
        if me is not None:
            perms = target.permissions_for(me)
            if not getattr(perms, 'view_channel', True) or not getattr(perms, 'send_messages', True):
                return (f'mere paas #{getattr(target, "name", "?")} mein post karne ki '
                        'permission nahi hai; woh permission do, ya koi aur channel batao.')
            if ping and not getattr(perms, 'mention_everyone', False):
                return f'mere paas #{getattr(target, "name", "?")} mein @everyone mention ki permission nahi hai; pehle woh permission do, ya quiet announce bolo.'
        draft = re.sub(r'<@!?\d+>|<@&\d+>|@everyone|@here', '', draft, flags=re.I).strip()[:400]
        if not draft:
            return 'draft khaali reh gaya; thoda detail do.'
        ident = secrets.token_hex(5)
        self.pending[ident] = {
            'author': message.author.id,
            'channel': message.channel.id,
            'target': target.id,
            'guild': message.guild.id,
            'ping': ping,
            'text': ('@everyone\n' if ping else '') + draft,
            'expires': time.monotonic() + self.PREVIEW_TTL,
            'due': due,
        }
        self._ledger_stash_preview(ident, self.pending[ident])
        self.last_preview = (message.channel.id, ident)
        channel_name = getattr(target, 'name', '?')
        return self._preview_text(self.pending[ident], channel_name)
