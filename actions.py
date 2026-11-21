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
requesting owner and the channel they were made in; the preview text itself
never pings (the normal reply path allows no everyone/role mentions), and the
member/role mention syntax inside the model's draft is stripped before it is
shown or posted - the only ping in the posted announcement is the code-added
@everyone the preview declared.
"""
import logging
import re
import secrets
import time

import discord

from music.player import MusicError
from music.ui import clean
import timed_messages as tm

log = logging.getLogger('kristy.actions')

DO_RE = re.compile(r"^\s*\[\[\s*do\s*:\s*([a-z]+(?:\s+[a-z]+){0,3})(?:\s+#([a-z0-9_-]{1,32}))?\s*(?:\|\s*([^\]]{5,600}?))?\s*\]\]", re.I)
CONFIRM_RE = re.compile(r"announce\s+(confirm|cancel)\s+([a-f0-9]{10})\s*$", re.I)

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
    "Rules: at most one marker per reply, only when the request is clear - "
    "ask a short question instead when it is not. NEVER claim you did one of "
    "these things in plain text; the marker is the only way, and the runtime "
    "reports the true outcome. For everything else (chat, questions, jokes) "
    "answer normally with no marker."
)


class Actions:
    PREVIEW_TTL = 300  # seconds

    def __init__(self, client, owner_id, schedule_store):
        self.client = client
        self.owner_id = (owner_id or '').strip()
        self.store = schedule_store
        self.pending = {}

    def is_owner(self, message):
        return bool(self.owner_id) and str(message.author.id) == self.owner_id

    async def handle(self, message, text):
        """Deterministic announce confirm/cancel. True when the message is consumed."""
        m = CONFIRM_RE.fullmatch((text or '').strip())
        if not m:
            return False
        verb, ident = m.group(1).lower(), m.group(2).lower()
        entry = self.pending.get(ident)
        if entry is None:
            await message.reply('Ye announcement preview nahi mila (expire ho gaya ya already done). Naya preview banao.', mention_author=False)
            return True
        if entry['author'] != message.author.id:
            await message.reply('Ye preview kisi aur ka hai; sirf jisne banaya wahi confirm ya cancel kar sakta hai.', mention_author=False)
            return True
        if message.channel.id != entry['channel']:
            await message.reply('Confirm wahi channel mein karo jahan preview bana tha.', mention_author=False)
            return True
        if time.monotonic() > entry['expires']:
            self.pending.pop(ident, None)
            await message.reply('Ye preview expire ho gaya; naya preview banao.', mention_author=False)
            return True
        self.pending.pop(ident, None)  # single-use for both verbs
        if verb == 'cancel':
            await message.reply('Announcement cancel kar diya; kuch post nahi hua.', mention_author=False)
            return True
        channel = self.client.get_channel(entry.get('target', entry['channel']))
        if channel is None:
            await message.reply('Channel ab accessible nahi hai; announcement post nahi hua.', mention_author=False)
            return True
        try:
            sent = await channel.send(entry['text'], allowed_mentions=discord.AllowedMentions(everyone=entry['ping'], users=False, roles=False))
        except Exception as exc:
            # The error may have happened after Discord accepted the message.
            # Never claim a definite failure and never auto-retry a duplicate.
            log.warning('announce send outcome unknown: %s', type(exc).__name__)
            await message.reply('Send ka result confirm nahi hua - ho sakta hai post ho gaya ho. Channel khud check karo; main apne aap dobara NAHI bhejungi. Naya preview chahiye toh phir se bolo.', mention_author=False)
            return True
        await message.reply(f'Announcement post ho gaya ✅ {sent.jump_url}', mention_author=False)
        return True

    async def maybe_execute(self, message, reply):
        """Execute a leading [[do:...]] marker; return the final reply text."""
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
            if cmd == 'announce everyone':
                return self._announce_preview(message, draft, ping=True, chan_name=chan_name)
            if cmd == 'announce quiet':
                return self._announce_preview(message, draft, ping=False, chan_name=chan_name)
            if cmd == 'announce':
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
        except OSError:
            return 'schedule storage abhi padh nahi payi; kuch confirm nahi kar sakti.'
        rows = []
        for e in entries:
            if e.get('guild') != message.guild.id or e.get('author') != message.author.id:
                continue
            # store schema: status is preview/scheduled/...; the pending vs
            # confirmed distinction lives on the reason field
            if e.get('status') not in ('preview', 'scheduled') \
                    or e.get('reason') not in ('awaiting_confirmation', 'confirmed'):
                continue
            rows.append(f"{e['id']} | {tm.shown(e['due'])} | {e['reason']}")
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
        want = chan_name.lower().replace('_', '-')
        pool = list(getattr(message.guild, 'text_channels', None)
                    or getattr(message.guild, 'channels', []) or [])
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

    def _announce_preview(self, message, draft, ping, chan_name=None):
        if message.guild is None:
            return 'announcement server channel mein banti hai, DM mein nahi.'
        if not self.owner_id:
            return 'announce abhi configured nahi hai (OWNER_ID set nahi).'
        if not self.is_owner(message):
            return 'announcement sirf server owner kara sakta hai.'
        target, clarify = self._resolve_channel(message, chan_name)
        if clarify:
            return clarify
        if not draft:
            return 'kis baare mein announce karna hai? thoda context do, phir draft bana dungi.'
        me = message.guild.me
        if ping and me is not None and not target.permissions_for(me).mention_everyone:
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
        }
        channel_name = getattr(target, 'name', '?')
        return ('announcement preview (abhi post NAHI hua):\n'
                f'channel: #{channel_name}\n'
                f'ping: {"everyone" if ping else "none (quiet post)"}\n'
                f'---\n{draft}\n---\n'
                f'bhejne ke liye: Kristy announce confirm {ident}\n'
                f'cancel ke liye: Kristy announce cancel {ident}\n'
                'preview 5 minute mein expire ho jayega.')
