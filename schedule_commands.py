"""Timed messages saved on a clear request (no confirm step); content after | is literal data."""
import re
import time

INTENT = re.compile(r"\b(remind(?:er)?|schedule|timer|yaad\s+dila(?:na|do|dena)?|reminder\s+laga)\b|\b\d+\s*(?:min(?:ute)?s?|hours?|ghant[ae])\s+baad\b.*\b(?:bhej|bol|bula|tag|ping|message)|\b(?:bhej\w*|bol\w*|bula\w*|tag\w*|ping\w*|yaad\s+dila\w*|message)\b[^\n|]*\b\d+\s*(?:min(?:ute)?s?|hours?|ghant[ae])\s+baad\b|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b.*\b(?:bhej|bol|bula|tag|ping|message)", re.I)
import discord

import agent_core
import timed_messages as tm
import logging
log = logging.getLogger('kristy.schedule_commands')

HELP = ('Kristy remind me 10 min baad pani peene\n'
        'Kristy remind me tagging Mayank 10 min baad to watch anime\n'
        'Kristy schedule kal 8 PM | #text-channel | Mayank, Naiyo | message\n'
        'Role ping bhi chalta hai: "tag @Role Name" ya native @role mention; role mentionable hona chahiye\n'
        'Kisi message ko reply karke: 5 min baad same message dena\n'
        'Repeats: every day 9 PM / every Monday 9 PM / every month on 15 at 9 PM\n'
        'schedule list / schedule cancel ID (repeat ID cancels entire series)')

# Natural free-form time phrases, converted into the strict deterministic
# grammar before parsing. Bare clock times resolve to today only; a passed
# time is rejected by tm.parse_time, never silently moved to tomorrow.
_NATURAL_TIME = [
    (re.compile(r'\bafter\s+(\d+)\s*(min(?:ute)?s?|hours?)\b', re.I), lambda m: f'{m[1]} {m[2]} baad'),
    (re.compile(r'\b\d+\s*(?:min(?:ute)?s?|hours?|ghant[ae])\s+baad\b', re.I), None),
    (re.compile(r'\b(?:aaj|today|kal|tomorrow)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b', re.I), None),
    (re.compile(r'\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', re.I), lambda m: f'aaj {m[1]}:{m[2] or "00"} {m[3]}'),
    (re.compile(r'\b(\d{1,2}):(\d{2})\s*(am|pm)\b', re.I), lambda m: f'aaj {m[1]}:{m[2]} {m[3]}'),
]
_TAG_NAME = re.compile(r'\b(?:tag(?:ging|ged)?|ping(?:ing|ed)?)\s+(?!(?:me|mujhe|mujhko|mujko|humhe|humko)\b)@?([\w.\-]+)', re.I)
_NAME_LIST = re.compile(r'\s*(?:,|\band\b|\baur\b)\s*@?([\w.\-]+)', re.I)
REPEAT = re.compile(r'\b(?:same|wahi|wohi)\b', re.I)
_SELF_WORDS = {'me', 'mujhe', 'mujhko', 'mujko', 'humhe', 'humko'}
_CONNECTORS = {'to', 'ki', 'ke', 'ko', 'ka', 'pe', 'par', 'per', 'that', 'mein', 'me', 'se', 'karke', 'please', 'plz', 'ok', 'okay', 'aur', 'ya'}
_HERE_WORDS = re.compile(r'\b(?:here|yahan|yahi|isi\s+channel(?:\s+(?:mein|me|pe|par))?|is\s+channel(?:\s+(?:mein|me|pe|par))?)\b', re.I)
_TAG_WORDS = re.compile(r'\b(?:by\s+)?tag(?:ging|ed)?\b|\bping(?:ing|ed)?\b', re.I)
# A direct "current one / yahin" answer to the destination question: every
# #mention in the original request is payload (e.g. a voice channel to join),
# and the reminder posts in the channel the conversation is in.
_HERE_ANSWER = re.compile(r'(?:current(?:\s+(?:one|channel))?|this\s+channel|isi\s+channel(?:\s+(?:mein|me|pe|par))?|is\s+channel(?:\s+(?:mein|me|pe|par))?|yahin|yahi|yahan|here|same\s+channel)[\s!.]*', re.I)
# Explicit fielded request form: "- message : ... - time : ... - ping : ...".
_STRUCTURED_FIELD = re.compile(r'(?:^|[-–—\n])\s*\b(message|msg|time|ping|tag|channel)\s*:\s*', re.I)
# Scheduling-command filler: a body made only of these has no payload.
_COMMAND_FILLER = _SELF_WORDS | _CONNECTORS | {'laga', 'lagana', 'lagao', 'set', 'karo', 'kar', 'karna', 'do', 'dena', 'dijiye', 'rakh', 'rakho', 'rakhna', 'chahiye', 'hai', 'hain', 'tha', 'thi', 'bhej', 'bhejna', 'bhejo', 'bol', 'bolna', 'bolo', 'bula', 'bulana', 'bulao', 'message', 'remind', 'reminder', 'yaad', 'dila', 'dilana', 'ek', 'mera', 'meri', 'mere', 'liye', 'na'}


def _strip_lead(text):
    """Leading self-references are targets; connectors before the payload are
    filler. A self word after a connector is payload ('ki mujhe khana...')."""
    tokens = text.split(); index = 0; seen_connector = False
    while index < len(tokens):
        word = tokens[index].strip('.,!').casefold()
        if not seen_connector and word in _SELF_WORDS: index += 1
        elif word in _CONNECTORS: seen_connector = True; index += 1
        else: break
    return ' '.join(tokens[index:])



_CLOCK=re.compile(r"\b(?:(aaj|today|kal|tomorrow)\s+)?(?:at\s+|@\s*)?(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm)\b",re.I)
_CLOCK24=re.compile(r"(?<![\d:-])(?:(\d{4}-\d{2}-\d{2})\s+|(?:at\s+)?)([01]?\d|2[0-3]):([0-5]\d)\b(?!\s*(?:am|pm))",re.I)
_REL=re.compile(r"\b(?:after|in)\s+(\d+)\s*(min(?:ute)?s?|hours?|hrs?)\b|\b(\d+)\s*(min(?:ute)?s?|hours?|hrs?|ghant[ae])\s+baad\b",re.I)
_APPROX=re.compile(r"(?:almost|around|approx\w*|about|nearly|lagbhag|takriban|roughly|~)\W*$",re.I)

def _tag_requested(text, name):
    """The named target appears next to the user's own tag/ping wording:
    after it ('tag Mayank', 'ping: VC Member', 'tag A, B'), or in Hindi
    post-position ('VC Member ko tag karna', '@role yeh wala tag use karke').
    Multi-word names are matched whole; the model cannot invent a target the
    user never named with a tag/ping marker."""
    words = r'\s+'.join(re.escape(w) for w in (name or '').split())
    if not words:
        return False
    after = rf'\b(?:tag(?:ging|ged)?|ping(?:ing|ed)?)\b\s*[:,.\-]?\s*(?:@?[\w.\-]+\s*(?:,|\band\b|\baur\b)\s*)*@?{words}(?![\w.])'
    before = rf'(?<![\w.])@?{words}\s+(?:(?:ko|yeh|ye|wala|wali|use|karke|kar|karna|please|plz)\s+)*(?:tag|ping)\b'
    # Hindi group target: 'VC Member role walo ko', 'mods wale ko' - clearly a
    # set of people to ping, not task content.
    walo = rf'(?<![\w.])@?{words}\s+(?:role\s+)?wal[oe]\s+ko\b'
    return bool(re.search(after, text, re.I) or re.search(before, text, re.I) or re.search(walo, text, re.I))


def _native_role_tagged(text, role_id):
    """A native <@&id> role mention the user clearly meant as a ping target:
    tag/ping word before it, or Hindi tag phrasing right after it."""
    after = rf'\b(?:tag(?:ging|ged)?|ping(?:ing|ed)?)\b\s*[:,.\-]?\s*(?:@?[\w.\-]+\s*(?:,|\band\b|\baur\b)\s*)*<@&{role_id}>'
    before = rf'<@&{role_id}>\s+(?:(?:ko|yeh|ye|wala|wali|use|karke|kar|karna|please|plz)\s+)*(?:tag|ping)\b'
    return bool(re.search(after, text, re.I) or re.search(before, text, re.I))


# Conditional follow-ups ('if I don't reply in 5 mins then message ...') are a
# capability the scheduler does not have; never silently save only part of one.
_CONDITIONAL = re.compile(
    r"\b(?:if|agar)\b[^.\n]{0,50}\b(?:don'?t|do not|dont|nahi|nhi|no|na)\b[^.\n]{0,25}\b(?:reply|respond|answer|jawab|message|aaye?|aata|mile)\b"
    r"|\bwarna\b[^.\n]{0,30}\b(?:message|reply|tag|ping|bhej|bol|bata)\b"
    r"|\botherwise\b[^.\n]{0,30}\b(?:message|reply|tag|ping|text)\b", re.I)

def time_candidates(text,anchor):
    """Time expressions the USER wrote, as (due, when_string, anchor, kind, approximate)."""
    found=[]
    local=anchor.astimezone(tm.IST)
    def add(when,m,kind):
        try:due=tm.parse_time(when,anchor)
        except ValueError:return
        before=text[max(0,m.start()-25):m.start()]
        approx=bool(_APPROX.search(before)) or before.count('(')>before.count(')')
        found.append((due,when,anchor,kind,approx))
    for m in _CLOCK.finditer(text):
        h=int(m[2]);mi=m[3] or '00'
        if not 1<=h<=12:continue
        add(f"{m[1] or 'aaj'} {h}:{mi} {m[4]}",m,'clock')
    for m in _CLOCK24.finditer(text):
        add(f"{m[1] or local.date().isoformat()} {int(m[2])}:{m[3]}",m,'clock')
    for m in _REL.finditer(text):
        n,unit=(m[1],m[2]) if m[1] else (m[3],m[4])
        add(f"{n} {'minute' if unit.lower().startswith('min') else 'hour'} baad",m,'rel')
    return found

# Recurrence is grounded in the human's weekday and clock, never invented by AI.
_WEEKDAY = re.compile(r'\b(?:every|har)\s+('+'|'.join(tm.WEEKDAYS)+r')\b', re.I)
_RECURRENCE = re.compile(r'\b(?:every|weekly|daily|monthly|weekdays|weekends|har|repeat(?:ing)?)\b', re.I)

def pick_weekly(ai_when, sources):
    text=' '.join(t for t,a in sources)
    days=list(_WEEKDAY.finditer(text))
    named_days=re.findall(r'\b('+'|'.join(tm.WEEKDAYS)+r')\b',text,re.I)
    if len({d.lower() for d in named_days})>1:
        return None,'Ek weekly series mein ek hi weekday do.'
    if not days:
        return None,'Weekly reminder ke liye ek weekday do, jaise every Monday at 9 PM.'
    if len({m[1].lower() for m in days})!=1:
        return None,'Ek weekly series mein ek hi weekday do.'
    if re.search(r'\b(?:UTC|GMT|PST|PDT|EST|EDT|CST|CDT|MST|MDT|BST|CET|CEST|JST|PT|ET|timezone|next)\b|\d{4}-\d{2}-\d{2}|\b(?:for|after)\s+\d+\s+weeks?\b',text,re.I):
        return None,'Weekly reminders abhi IST (Asia/Kolkata) mein hain, bina start/end date. IST time confirm karke likho.'
    clocks=list(_CLOCK.finditer(text))
    if not clocks or len({(m[2],m[3] or '00',m[4].lower()) for m in clocks})!=1:
        return None,'Weekly reminder ka ek exact time aur AM/PM do, jaise every Monday at 9 PM.'
    if re.search(r'\b(?:daily|weekdays|weekends|other|alternate|until|till|starting|start|from)\b|\bevery\s+\d',text,re.I):
        return None,'Abhi sirf har hafte ek weekday supported hai, bina end date. Exact weekly time do.'
    m=clocks[0];when=f'every {days[0][1].lower()} {m[2]}:{m[3] or "00"} {m[4].lower()}'
    anchor=sources[0][1]
    try:
        expected,r=tm.weekly_time(when,anchor)
        actual,ar=tm.weekly_time(ai_when,anchor)
        if expected!=actual or r!=ar:raise ValueError
    except (ValueError,TypeError):
        return None,'Weekly time clearly samajh nahi aaya. Jaise: every Monday at 9 PM.'
    return (when,anchor),None

_DAILY_WORDS=re.compile(r'\b(?:daily|every\s+day|har\s+din)\b',re.I)
_MONTHLY_DAY=re.compile(r'\b(?:every\s+month|monthly|har\s+mahine)\s+(?:on\s+)?(?:the\s+)?(3[01]|[12]\d|[1-9])(?:st|nd|rd|th)?\b',re.I)

def pick_recurrence(ai_when,sources):
    text=' '.join(t for t,a in sources)
    daily=bool(_DAILY_WORDS.search(text));monthly=bool(re.search(r'\b(?:monthly|every\s+month|har\s+mahine)\b',text,re.I))
    weekly=bool(_WEEKDAY.search(text))
    if not daily and not monthly:return pick_weekly(ai_when,sources)
    if sum((daily,monthly,weekly))!=1:
        return None,'Ek series mein ek hi repeat rule do: daily, weekly ya monthly.'
    if re.search(r'\b(?:UTC|GMT|PST|PDT|EST|EDT|CST|CDT|MST|MDT|BST|CET|CEST|JST|PT|ET|timezone|next|other|alternate|until|till|starting|start|from)\b|\d{4}-\d{2}-\d{2}|\bevery\s+\d|\b(?:for|after)\s+\d+\s+(?:days?|weeks?|months?)\b',text,re.I):
        return None,'Abhi IST mein daily, weekly ya fixed-date monthly repeats hain, bina start/end date. Exact repeat time do.'
    if re.search(r'\b(?:weekdays|weekends|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',text,re.I):
        return None,'Daily/monthly ke saath weekday rule mix nahi kar sakti. Ek repeat rule do.'
    clocks=list(_CLOCK.finditer(text))
    if not clocks or len({(int(m[2]),int(m[3] or 0),m[4].lower()) for m in clocks})!=1:
        return None,'Repeat ka ek exact time aur AM/PM do, jaise every day at 9 PM.'
    m=clocks[0];clock=f'{m[2]}:{m[3] or "00"} {m[4]}'
    if monthly:
        days=list(_MONTHLY_DAY.finditer(text))
        if not days and len(sources)>1:
            # A date-only answer completes a monthly clarification, not a new goal.
            follow=sources[-1][0]
            dm=re.search(r'\bon\s+(?:the\s+)?(3[01]|[12]\d|[1-9])(?:st|nd|rd|th)?\b',follow,re.I)
            if dm:days=[dm]
        if len(days)!=1 or re.search(r'\b(?:last|second|third|first|fourth|fifth)\b|\b\d+(?:st|nd|rd|th)?\s*(?:and|aur|,)\s*\d+',text,re.I):
            return None,'Monthly reminder ki ek date do (1-28), jaise every month on 15 at 9 PM.'
        when=f'every month on {days[0][1]} {clock}'
    else:when=f'every day {clock}'
    anchor=sources[0][1]
    try:
        due,r=tm.recurrence_time(when,anchor);ai_due,ai_r=tm.recurrence_time(ai_when,anchor)
        if due!=ai_due or r!=ai_r:raise ValueError
    except (ValueError,TypeError):
        return None,'Repeat time clearly samajh nahi aaya. Date/time aur AM/PM ke saath dobara likho.'
    return (when,anchor),None

def pick_time(ai_when,sources):
    """(when_string, anchor) the user's own words support AND the model agrees with;
    otherwise (None, short question). Latest source with a time wins."""
    for text,anchor in reversed(sources):
        cands=time_candidates(text,anchor)
        if not cands:continue
        clocks=[c for c in cands if c[3]=='clock'];rels=[c for c in cands if c[3]=='rel']
        if clocks:
            pool=clocks
            if any(not r[4] and abs(r[0]-clocks[0][0])>120 for r in rels):
                return None,'Do alag time dikh rahe hain. Exact time kaunsa? Jaise: aaj 3:30 PM'
        else:pool=rels
        if len({round(c[0]) for c in pool})>1 and max(c[0] for c in pool)-min(c[0] for c in pool)>1:
            return None,'Ek se zyada time dikh rahe hain. Exact time ek hi likho.'
        due,when,a,_,_=pool[0]
        try:agreed=abs(tm.parse_time(ai_when,a)-due)<=1
        except ValueError:agreed=False
        if not agreed:return None,'Time clearly samajh nahi aaya. Kab chahiye? Jaise: aaj 3:30 PM'
        return (when,a),None
    return None,'Kab? Time likho, jaise: aaj 3:30 PM ya 10 minute baad'

def _guild_role(guild,role_id):
    getter=getattr(guild,'get_role',None)
    role=getter(role_id) if getter else None
    if role is None:return None
    is_default=getattr(role,'is_default',None)
    if callable(is_default) and is_default():return None
    return role


class Commands:
    def __init__(self,store,resolver,owner_id='',ledger=None):
        self.store,self.resolver,self.owner_id=store,resolver,owner_id
        self.pending={};self.helper_addressed={};self.intent=None;self.health=lambda:'unknown';self.bot_id=None;self.client_for_access=None
        # Optional: recent same-channel lines [(author_name, author_id, text)].
        # Lets a 'set kardo' after Kristy's own restatement resolve instead of
        # re-asking everything; validation still uses only the user's own words.
        self.history_provider=None
        self.role_resolver=None
        self.ledger=ledger
        self.executor=agent_core.Executor(ledger) if ledger is not None else None
    PENDING_KINDS=('reminder_clarify','reminder_confirm')
    def _ledger_stash(self,kind,key,item):
        if self.ledger is None:return
        try:
            self.ledger.settle_key(self.PENDING_KINDS,*key,agent_core.EXPIRED)
            self.ledger.create(kind,agent_core.PENDING,guild=key[0],channel=key[1],author=key[2],
                               payload=agent_core.pack_pending_item(item),
                               expires=agent_core.wall_from_monotonic(item[0]))
        except Exception:
            log.warning('ledger stash failed',exc_info=True)
    def _ledger_drop(self,key,status=agent_core.EXPIRED):
        if self.ledger is None:return
        try:self.ledger.settle_key(self.PENDING_KINDS,*key,status)
        except Exception:
            log.warning('ledger drop failed',exc_info=True)
    def _pending_pop(self,key,status=agent_core.EXPIRED):
        self.pending.pop(key,None)
        self._ledger_drop(key,status)
    def restore_pending(self):
        """Reload unexpired pending clarifications/confirms after a restart,
        so a 'haan'/'set kardo' still resolves against the original request."""
        if self.ledger is None:return 0
        restored=0
        for kind in self.PENDING_KINDS:
            for row in self.ledger.pending(kind):
                try:
                    key=(int(row['guild']),int(row['channel']),int(row['author']))
                    self.pending[key]=agent_core.unpack_pending_item(row['payload'],row['expires'])
                    restored+=1
                except Exception:
                    log.warning('ledger restore skipped a row',exc_info=True)
        return restored
    def key(self,message):return message.guild.id,message.channel.id,message.author.id
    def pending_item(self,message):
        now=time.monotonic();self.pending={k:v for k,v in self.pending.items() if v[0]>now}
        item=self.pending.get(self.key(message))
        ref=getattr(message,'reference',None)
        if item and ref is not None and getattr(ref,'message_id',None)!=item[1]:return None
        return item
    def helper_item(self,message,addressed):
        """Another member's open clarification in this channel that THIS message
        can complete: exactly one pending from someone else, and the message
        replies to that clarification or is addressed to Kristy. Ambiguous or
        unaddressed chat is never hijacked into someone else's request."""
        now=time.monotonic();self.pending={k:v for k,v in self.pending.items() if v[0]>now}
        found=None
        for k,v in self.pending.items():
            if k[0]==message.guild.id and k[1]==message.channel.id and k[2]!=message.author.id:
                if found is not None:return None
                found=(k,v)
        if found is None:return None
        ref=getattr(message,'reference',None)
        if ref is not None and getattr(ref,'message_id',None)==found[1][1]:return found
        if addressed:return found
        return None
    def mark_helper_addressed(self,message,addressed):
        # discord.Message uses __slots__: never set attributes on it. Remember by message id, bounded.
        key=getattr(message,'id',None)
        if key is None:key=('obj',id(message))
        self.helper_addressed[key]=bool(addressed)
        while len(self.helper_addressed)>500:self.helper_addressed.pop(next(iter(self.helper_addressed)))
    def was_helper_addressed(self,message):
        return bool(self.helper_addressed.get(getattr(message,'id',None) if getattr(message,'id',None) is not None else ('obj',id(message)),False))
    def followup(self,message,text,addressed=True):
        """A reply to our own clarification (same guild/channel/author, within
        2 minutes) - or a helper's addressed/reply completion of someone else's.
        Anything said then goes to the intent step with the original request;
        the model may say it is unrelated and normal chat continues."""
        item=self.pending_item(message)
        if item:
            ref=getattr(message,'reference',None)
            return ref is not None or bool(addressed)
        found=self.helper_item(message,addressed)
        if found is not None:
            # handle() re-derives addressed-ness; carry this gate's answer.
            self.mark_helper_addressed(message,addressed)
        return found is not None
    async def reply(self,message,text):
        return await message.reply(text,mention_author=False,allowed_mentions=discord.AllowedMentions.none())
    async def clarify(self,message,text='Kab aur kya? Jaise: Kristy remind me 10 min baad pani peene',request=None,anchor=None,key=None,consent=None):
        """Ask a short question and remember the ORIGINAL request text and its
        message time, so the user's answer completes that request. When consent
        is given, an affirmative answer ('haa', 'yes', ...) counts as the user's
        own approval of the proposed tag wording."""
        sent=await self.reply(message,text)
        if len(self.pending)>=1000:self.pending.pop(next(iter(self.pending)))
        pending=(time.monotonic()+120,getattr(sent,'id',None),request,anchor or getattr(message,'created_at',None))
        if consent is not None:pending=pending+('__tags__',consent)
        key=key or self.key(message)
        self.pending[key]=pending
        self._ledger_stash('reminder_clarify',key,pending)

    def tag_names(self,text,guild):
        """'tagging X' / 'tag X, Y' -> (<@id> strings, text without them).
        An unresolvable name is an error, never left in the message body."""
        found=[]
        while True:
            m=_TAG_NAME.search(text)
            if not m:break
            names=[m[1].strip('.,!')];end=m.end()
            while True:
                n=_NAME_LIST.match(text,end)
                if not n:break
                cand=n[1].strip('.,!')
                if self.resolver(guild,cand)[0] is None:break
                names.append(cand);end=n.end()
            for name in names:
                member=self.resolver(guild,name)[0]
                if member is None or member.bot:raise ValueError(f'"{name}" member nahi mila; exact username ya @mention do.')
                found.append(f'<@{member.id}>')
            text=text[:m.start()]+' '+text[end:]
        return found,text

    def natural_parts(self,kind,rest,message):
        """Free-form remind/schedule/summon -> strict parts, or None when the
        request is genuinely ambiguous. Identity comes from the message
        author, never from a typed username. Everything still lands as an
        unarmed preview showing the exact outgoing text."""
        if _RECURRENCE.search(rest):return None  # never degrade a series to one-shot in fallback
        text=rest
        dests=re.findall(r'<#(\d+)>',text)
        if len(dests)>1:return None
        dest=f'<#{dests[0]}>' if dests else 'here'
        text=re.sub(r'<#\d+>',' ',text)
        text=re.sub(r'\([^)]*\buser\s*name\b[^)]*\)',' ',text,flags=re.I)
        when=None
        for pattern,convert in _NATURAL_TIME:
            m=pattern.search(text)
            if m:
                when=convert(m) if convert else m[0].strip()
                text=text[:m.start()]+' '+text[m.end():]
                break
        if when is None:return None
        mentions=re.findall(r'<@!?(\d+)>',text)
        text=re.sub(r'<@!?\d+>',' ',text)
        tagged,text=self.tag_names(text,message.guild)
        mentions+=[t[2:-1] for t in tagged]
        text=_TAG_WORDS.sub(' ',text)
        text=_HERE_WORDS.sub(' ',text)
        self_target=bool(re.search(r'\b(?:me|mujhe|mujhko|mujko|humhe|humko)\b',text,re.I))
        body=_strip_lead(text).strip(' .,!')
        if body and not any(t.strip('.,!').casefold() not in _COMMAND_FILLER for t in body.split()):body=''
        if kind=='remind' and not mentions:
            return [when,dest,body] if body else None
        if kind=='remind':
            # 'remind me tagging X': author (only if 'me') plus the tagged people.
            if not body:return None
            kind='schedule'
        targets=list(dict.fromkeys(mentions))
        if self_target and str(message.author.id) not in targets:targets.insert(0,str(message.author.id))
        if kind=='summon':
            return [when,dest,', '.join(f'<@{t}>' for t in targets)] if targets else None
        if not body:return None
        return [when,dest,', '.join(f'<@{t}>' for t in targets) or 'none',body]

    def structured_parts(self,kind,text,message,anchor):
        """Explicit '- message : ... - time : ... - ping : ...' form, parsed in
        code only. Every field is literal data from the user's own request, so
        no model call and no clarify loop: the recurrence rule, channel
        mentions inside the payload and the role audience survive exactly as
        written. Returns (kind, parts) or None when the complete form is
        absent; raises ValueError with the honest reason on a bad field."""
        marks=list(_STRUCTURED_FIELD.finditer(text))
        if not marks:return None
        fields={}
        for i,mk in enumerate(marks):
            name={'msg':'message','tag':'ping'}.get(mk[1].casefold(),mk[1].casefold())
            end=marks[i+1].start() if i+1<len(marks) else len(text)
            value=text[mk.end():end].strip(' \t\r\n-–—')
            if name in fields:
                # the same request re-seen via pending/history repeats fields
                # verbatim; a genuinely conflicting repeat stays ambiguous
                if fields[name]==value:continue
                return None
            fields[name]=value
        body=fields.get('message') or ''
        when=fields.get('time') or ''
        if not body or not when:return None
        when=' '.join(when.split())
        if re.fullmatch(r'(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)',when,re.I):
            when=f'aaj {when}'  # bare clock: same today-rule as natural time
        dest='here'
        chan=fields.get('channel') or ''
        if chan:
            cm=re.fullmatch(r'<#(\d+)>',chan.strip())
            if not cm:raise ValueError('Channel field native #channel mention hona chahiye.')
            dest=f'<#{cm[1]}>'
        targets=[];roles=[]
        ping=fields.get('ping') or ''
        if ping:
            for raw in re.split(r'\s*,\s*|\s+and\s+|\s+aur\s+',ping):
                raw=raw.strip()
                if not raw:continue
                mm=re.fullmatch(r'<@!?(\d+)>',raw);rr=re.fullmatch(r'<@&(\d+)>',raw)
                if mm:
                    member=message.guild.get_member(int(mm[1]))
                    if member is None or getattr(member,'bot',False):raise ValueError('Ping field ka member verify nahi hua.')
                    targets.append(member);continue
                if rr:
                    role=_guild_role(message.guild,int(rr[1]))
                    if role is None:raise ValueError('Ping field ka role verify nahi hua.')
                    roles.append(role);continue
                name=raw.lstrip('@').strip()
                member=self.resolver(message.guild,name)[0]
                if member is not None:
                    if getattr(member,'bot',False):raise ValueError(f'"{name}" member nahi mila; exact username ya @mention do.')
                    targets.append(member);continue
                role=self.find_role(message.guild,name)
                if role is None:raise ValueError(f'"{name}" member ya role nahi mila; exact name ya native @mention do.')
                roles.append(role)
        if not targets and not roles:
            return 'remind',[when,dest,body]
        seen={message.author.id};tt=[f'<@{message.author.id}>']
        for t in targets:
            if t.id in seen:continue
            seen.add(t.id);tt.append(f'<@{t.id}>')
        rseen=set()
        for r in roles:
            if r.id in rseen:continue
            rseen.add(r.id);tt.append(f'<@&{r.id}>')
        return 'schedule',[when,dest,', '.join(tt),body]

    async def referenced(self,message):
        ref=message.reference
        target=getattr(ref,'resolved',None)
        if target is None or not hasattr(target,'content'):
            try:target=await message.channel.fetch_message(ref.message_id)
            except Exception:return None
        return target

    async def repeat(self,message,text):
        """'<time> baad same message dena' as a reply: re-send the replied-to
        message (Kristy's own or the requester's own) in the same channel.
        The quoted text is data only: its mentions become targets, nothing in
        it is executed, and broadcast/channel/command markers are refused."""
        if _RECURRENCE.search(text):
            await self.reply(message,'Repeating reminder naya task likhkar banao: remind me every Monday at 9 PM to study. Same-message shortcut one-time hai.');return True
        time_part=None;rest=text
        for pattern,convert in _NATURAL_TIME:
            m=pattern.search(rest)
            if m:time_part=convert(m) if convert else m[0].strip();break
        if time_part is None:
            await self.clarify(message);return True
        try:
            ref=await self.referenced(message)
            if ref is None or getattr(getattr(ref,'channel',None),'id',None)!=message.channel.id:
                await self.clarify(message);return True
            bot_id=self.bot_id() if self.bot_id else None
            if ref.author.id not in (bot_id,message.author.id):
                raise ValueError('Sirf Kristy ka ya tumhara apna message repeat kar sakti hoon.')
            guild=message.guild
            ids=list(dict.fromkeys(int(i) for i in re.findall(r'<@!?(\d+)>',ref.content or '')))
            body=re.sub(r'<@!?\d+>',' ',ref.content or '')
            body=' '.join(body.split())
            if not body:
                await self.clarify(message);return True
            if len(body)>1000 or re.search(r'@everyone|@here|<@|<#|\[\[(?:tag|search):',body,re.I):
                raise ValueError('Is message ko repeat nahi kar sakti (mention/command/lamba text).')
            targets=[]
            for i in ids:
                member=guild.get_member(i)
                if member is None or member.bot:raise ValueError('Message ka koi tagged member ab verify nahi hua.')
                targets.append(member)
            if len(targets)>5:raise ValueError('Maximum 5 targets.')
            anchor=getattr(message,'created_at',None)
            if anchor is None:raise ValueError('Original message ka time verify nahi hua.')
            now=time.time();due=tm.parse_time(time_part,anchor)
            if due<=now:raise ValueError('Time nikal gaya; naya time do.')
            full=(' '.join(f'<@{t.id}>' for t in targets)+'\n' if targets else '')+body
            draft=dict(guild=guild.id,author=message.author.id,source=message.channel.id,dest=message.channel.id,targets=[t.id for t in targets],body=full,due=due)
            tm.access(self.client_for_access,draft)
            self.store.create(**draft,now=now,armed=True)
            await self.reply(message,'OK')
        except (ValueError,TypeError) as exc:
            await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Schedule data invalid hai; kuch save nahi hua.')
        except OSError:
            await self.reply(message,'Schedule storage read/write nahi hua; success confirm nahi karungi.')
        return True

    async def ai_request(self,message,text,client,fallback=None,kind='remind',pkey=None,helper=None):
        """The model only UNDERSTANDS the request (structured intent). Every
        effect is decided here: time via tm.parse_time against the ORIGINAL
        message time, targets resolved to real members, same channel unless a
        native #channel mention is in the user's own text, and nothing is
        confirmed to the user before the job is on disk. A helper may complete
        someone else's open clarification (pkey set): the original requester
        still confirms before anything is saved."""
        self.client_for_access=client
        key=pkey or self.key(message)
        if pkey is not None:
            now=time.monotonic();self.pending={k:v for k,v in self.pending.items() if v[0]>now}
            item=self.pending.get(key)
        else:
            item=self.pending_item(message)
        if item and len(item)>=6 and item[2]=='__confirm__':
            # The original requester answers a helper-filled preview; only
            # THEIR haan/nahi settles it, never the helper's.
            if key[2]!=message.author.id:
                await self.reply(message,f'Yeh <@{key[2]}> ka reminder hai; unke haan/nahi ke bina save nahi hoga.')
                return True
            anchor=item[3];ckind,parts=item[4],item[5]
            ans=' '.join(text.strip().lower().split())
            if re.fullmatch(r'(?:haan|ha|han|yes|yeah|ok|okay|sahi|theek|done|bilkul|haan ji|kar do|set kar do)[\s!.]*',ans):
                try:
                    await self._commit(message,client,ckind,parts,time.time(),anchor)
                    self._pending_pop(key)
                except (ValueError,TypeError) as exc:
                    self._pending_pop(key)
                    await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Schedule data invalid hai; kuch save nahi hua.')
                except OSError:
                    await self.reply(message,'Schedule storage read/write nahi hua; success confirm nahi karungi.')
                return True
            if re.fullmatch(r'(?:nahi|nhi|no|mat|nope|cancel|rehne do)[\s!.]*',ans):
                self._pending_pop(key,agent_core.CANCELLED)
                await self.reply(message,'Theek hai, reminder save nahi hua.')
                return True
            await self.reply(message,"'haan' ya 'nahi' bolo; reminder abhi save nahi hua.")
            return True
        orig,anchor=(item[2],item[3]) if item else (None,None)
        # An affirmative answer to a tag-consent question IS the user's tag
        # choice; substitute the proposed wording so the checks below see it.
        if item and len(item)>=6 and item[4]=='__tags__':
            ans=' '.join(text.strip().lower().split())
            consent=item[5]
            proposed=consent[4:].strip().lower() if consent.startswith('tag ') else ''
            # 'haa'/'yes' - or simply repeating the proposed name - both answer
            # the tag question with a yes and count as the user's own tag choice.
            if (re.fullmatch(r'(?:ha+|haan|han|yes|yeah|yup|ok|okay|sahi|theek|bilkul|kar\s+do|haa?\s+ji|haan\s+ji)[\s!.]*',ans)
                    or (proposed and ans==proposed)):
                text=consent
        anchor=anchor or getattr(message,'created_at',None)
        request=(orig+'\n'+text) if orig else text
        # Conversational-to-action continuity: the model sees the recent channel
        # chat, so a follow-up like 'set kardo' after her own restatement is
        # understood. VALIDATION below still uses only the user's own words:
        # the pending original, this message, and the user's own recent lines.
        user_hist=[]
        if self.history_provider is not None:
            try:hist=list(self.history_provider(message) or [])
            except Exception:hist=[]
            seen={' '.join((orig or '').casefold().split()),' '.join(text.casefold().split())}
            for a,aid,content in hist[-8:]:
                if aid is not None and aid==message.author.id and isinstance(content,str) and content.strip():
                    norm=' '.join(content.casefold().split())
                    if norm in seen:continue  # the pending original and the current line never count twice
                    seen.add(norm)
                    user_hist.append(content.strip())
            chat_lines=[f'{a}: {content}' for a,aid,content in hist[-8:] if isinstance(content,str) and content.strip()]
        else:
            chat_lines=[]
        if user_hist:
            request=(orig+'\n' if orig else '')+'\n'.join(user_hist[-3:]+[text])
        if anchor is None:
            await self.reply(message,'Original message ka time verify nahi hua; kuch save nahi hua.');return True
        # An explicit '- message : ... - time : ... - ping : ...' request is
        # complete and literal: code parses and saves it directly, no model
        # call, no clarify loop, and recurrence/role/payload survive intact.
        # Parsed from the current line (or the pending original) only: the
        # history-augmented request can carry a second copy whose fields would
        # run into the first copy's last field.
        try:
            formed=self.structured_parts(kind,text,message,anchor)
            if formed is None and orig:
                formed=self.structured_parts(kind,orig,message,anchor)
        except (ValueError,TypeError) as exc:
            await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Request data invalid hai; kuch save nahi hua.')
            return True
        if formed is not None:
            fkind,fparts=formed
            try:
                await self._commit(message,client,fkind,fparts,time.time(),anchor)
                self._pending_pop(key)
            except (ValueError,TypeError) as exc:
                await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Request data invalid hai; kuch save nahi hua.')
            except OSError:
                await self.reply(message,'Schedule storage read/write nahi hua; success confirm nahi karungi.')
            return True
        # Conditional follow-ups ('if I don't reply in 5 mins then ...') are not
        # a scheduler capability. Ask once on the first turn instead of silently
        # saving only part of the request; the kept text drops the condition so a
        # consenting follow-up is not confused by its extra time words.
        if orig is None:
            cond=_CONDITIONAL.search(request)
            if cond:
                kept=request[:cond.start()].strip(' .,!?') or request
                await self.clarify(message,'Conditional reminder (jaise "agar reply na aaye toh message karna") abhi supported nahi hai. Bina condition ka plain reminder set kar doon? Time aur kaam wahi rakhti hoon.',kept,anchor)
                return True
        intent=None
        if self.intent is not None:
            model_text=text
            if chat_lines:
                model_text='[recent channel chat]\n'+'\n'.join(chat_lines)+'\n[latest message]\n'+text
            try:intent=await self.intent(orig,model_text,anchor)
            except Exception:intent=None
        if self.intent is None:
            await self.clarify(message,request=request,anchor=anchor,key=key);return True
        if not isinstance(intent,dict) or intent.get('action') not in ('remind','need_info','none'):
            parts=fallback() if fallback else None
            if parts:
                try:
                    await self._commit(message,client,kind,parts,time.time(),getattr(message,'created_at',None))
                    self._pending_pop(key)
                except (ValueError,TypeError) as exc:
                    await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Schedule data invalid hai; kuch save nahi hua.')
                except OSError:
                    await self.reply(message,'Schedule storage read/write nahi hua; success confirm nahi karungi.')
                return True
            await self.clarify(message,'Abhi samajh nahi paayi, reminder save nahi hua. Time aur kya yaad dilana hai ek line mein likho.',request,anchor,key=key);return True
        action=intent['action']
        if action=='none':return False
        if action=='need_info':
            q=intent.get('question')
            q=' '.join(q.split())[:150] if isinstance(q,str) and q.strip() else 'Kab aur kya?'
            q=re.sub(r'<[@#]|@(?:everyone|here)','',q)
            await self.clarify(message,q,request,anchor,key=key);return True
        try:
            when,task=intent.get('when'),intent.get('task')
            names=intent.get('tag_names') or []
            if not isinstance(when,str) or not isinstance(task,str) or not isinstance(names,list) or len(names)>5:raise TypeError
            when=' '.join(when.split());task=' '.join(task.split()).strip(' .,!')
            if not task or not when or len(when)>40 or len(task)>300:
                await self.clarify(message,'Kab aur kya? Time aur reminder ka kaam ek line mein likho.',request,anchor,key=key);return True
            # A condition inside the task is an unsupported capability, stated
            # plainly instead of saving it as flat reminder text.
            if _CONDITIONAL.search(task):
                await self.clarify(message,'Reminder ke kaam mein condition (if/agar/warna) save nahi kar sakti. Bina condition ka kaam ek line mein likho.',request,anchor,key=key);return True
            # The task must be the user's own words, not a model invention.
            words=[w for w in re.findall(r"[\w']+",task.casefold()) if len(w)>=3]
            low=request.casefold()
            if not words or sum(w in low for w in words)*2<len(words):
                await self.clarify(message,'Kya yaad dilana hai? Ek line mein likho.',request,anchor,key=key);return True
            # The time comes from the user's words (clock or duration), computed
            # here; the model's `when` only has to AGREE with it.
            sources=[(orig,anchor)] if orig else []
            sources.append((text,getattr(message,'created_at',None) or anchor))
            for h in user_hist[-3:]:
                sources.append((h,getattr(message,'created_at',None) or anchor))
            chosen,problem=(pick_recurrence(when,sources) if _RECURRENCE.search(request) else pick_time(when,sources))
            if chosen is None:
                await self.clarify(message,problem,request,anchor,key=key);return True
            when,when_anchor=chosen
            dests=list(dict.fromkeys(re.findall(r'<#(\d+)>',request)))
            # An explicit "current one / yahin" answer to the destination
            # question: every #mention in the request stays message payload
            # (e.g. a voice channel to join), the reminder posts here.
            here_answer=bool(orig) and bool(_HERE_ANSWER.fullmatch(text.strip()))
            if len(dests)>1 and not here_answer:raise ValueError('Ek hi destination channel do.')
            if dests and not here_answer and not re.search(rf'(?:\b(?:in|to|on|at)\s+<#{dests[0]}>|<#{dests[0]}>\s*(?:mein|me|pe|par|channel)\b|\bchannel\s+<#{dests[0]}>)',request,re.I):
                await self.clarify(message,'Kis channel mein bhejna hai? "<#channel> mein" likho, ya bina channel ke yahin bhejti hoon.',request,anchor,key=key);return True
            dest='here' if here_answer or not dests else f'<#{dests[0]}>'
            # A helper only supplies details; the reminder always belongs to
            # the member who asked for it, never to the helper.
            targets=[str(key[2])]
            mentioned=re.findall(r'<@!?(\d+)>',request)
            for m_id in mentioned:
                if not re.search(rf'\b(?:tag(?:ging)?|ping(?:ing)?)\s+(?:[\w.\-]+\s*(?:,|and|aur)\s*)*<@!?{m_id}>',request,re.I):
                    await self.clarify(message,'Kisko tag karna hai? "tag @naam" likho.',request,anchor,key=key,consent=f'tag <@!{m_id}>');return True
            targets+=mentioned
            roles=[]
            for r_id in re.findall(r'<@&(\d+)>',request):
                # A native role mention must be the user's own ping choice.
                if not _native_role_tagged(request,r_id):
                    await self.clarify(message,'Kis role ko tag karna hai? "tag @role" likho.',request,anchor,key=key,consent=f'tag <@&{r_id}>');return True
                roles.append(r_id)
            for name in names:
                if not isinstance(name,str) or not name.strip() or len(name)>40:raise TypeError
                clean=name.strip().lstrip('@')
                # a tagged target must be named with tag/ping wording in the user's text
                if not _tag_requested(request,clean):
                    await self.clarify(message,f'"{clean}" ko tag karna hai? "tag {clean}" likho.',request,anchor,key=key,consent=f'tag {clean}');return True
                member=self.resolver(message.guild,clean)[0]
                if member is not None:
                    if member.bot:raise ValueError(f'"{name}" member nahi mila; exact username ya @mention do.')
                    targets.append(str(member.id));continue
                role=self.find_role(message.guild,clean)
                if role is None:raise ValueError(f'"{name}" member ya role nahi mila; exact name ya native @mention do.')
                roles.append(str(role.id))
            targets=list(dict.fromkeys(targets));roles=list(dict.fromkeys(roles))
            # the model cannot drop the author's tag unless the user said so
            tag_author=not (intent.get('tag_author') is False and re.search(r"(?:don't|do not|dont|mat|without|no)\s+tag|tag\s+(?:mat|nahi|nhi)",request,re.I))
            if len(targets)==1 and tag_author and not roles:
                parts=[when,dest,task];kind='remind'
            else:
                parts=[when,dest,', '.join([f'<@{t}>' for t in targets]+[f'<@&{r}>' for r in roles]),task];kind='schedule'
            if helper is not None and key[2]!=message.author.id:
                # A helper completed the details; the original requester
                # confirms before anything is saved.
                target_member=message.guild.get_member(key[2])
                helper_name=getattr(message.author,'display_name','Kisi')
                target_name=getattr(target_member,'display_name','requester')
                sent=await message.reply(
                    f'{helper_name} ne details diye hain. <@{key[2]}>, reminder: "{task}" - {when}. Sahi hai? '
                    "'haan' bolo toh save karti hoon, 'nahi' toh cancel.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions(users=[target_member] if target_member is not None else False))
                self.pending[key]=(time.monotonic()+300,getattr(sent,'id',None),'__confirm__',when_anchor,kind,parts)
                self._ledger_stash('reminder_confirm',key,self.pending[key])
                return True
            await self._commit(message,client,kind,parts,time.time(),when_anchor)
            self._pending_pop(key)
        except (ValueError,TypeError) as exc:
            # A user-fixable input problem (unknown name, passed time, channel
            # ambiguity) keeps the open clarification so the follow-up answer
            # completes the SAME request; only invalid model data drops it.
            if isinstance(exc,TypeError):self._pending_pop(key)
            await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Request data invalid hai; kuch save nahi hua.')
        except OSError:
            await self.reply(message,'Schedule storage read/write nahi hua; success confirm nahi karungi.')
        return True

    def find_role(self,guild,name):
        """Resolve a role by the exact name the user gave; never a guess.
        Single exact non-default match only; anything else stays unclear."""
        want=(name or '').strip().lstrip('@').casefold()
        if not want or guild is None:return None
        if self.role_resolver is not None:
            return self.role_resolver(guild,name)[0]
        matches=[r for r in (getattr(guild,'roles',None) or [])
                 if not getattr(r,'is_default',lambda:False)() and getattr(r,'name','').casefold()==want]
        return matches[0] if len(matches)==1 else None

    async def _commit(self,message,client,kind,parts,now,anchor):
        guild=message.guild
        reminder=kind=='remind'
        if reminder and len(parts)==4:kind='schedule'
        when,destination=parts[:2]
        if anchor is None:raise ValueError('Original message ka time verify nahi hua.')
        recurrence=None
        if re.match(r'^(every|har|daily|monthly)\b',when,re.I):due,recurrence=tm.recurrence_time(when,anchor)
        else:due=tm.parse_time(when,anchor)
        if due<=now:raise ValueError('Time nikal gaya; naya time do.')
        if destination.casefold()=='here':dest=message.channel
        elif re.fullmatch(r'<#\d+>',destination):dest=guild.get_channel(int(destination[2:-1]))
        else:raise ValueError('Destination here ya ek native #text-channel mention hona chahiye.')
        if dest is None:raise ValueError('Destination is server mein verify nahi hua.')
        if getattr(dest,'type',discord.ChannelType.text)!=discord.ChannelType.text:
            raise ValueError('Reminder ping sirf text channel mein ja sakta hai; voice channel mein nahi. Text channel do ya bina channel ke yahin bhejo.')
        targets=[];roles=[]
        if kind=='remind':targets=[message.author];body=parts[2]
        else:
            raw_targets=parts[2]
            body='VC join kar lo, creator ne is time par bulaya hai.' if kind=='summon' else parts[3]
            if raw_targets.casefold() not in ('none','koi nahi'):
                for raw in re.split(r'\s*,\s*|\s+aur\s+|\s+and\s+',raw_targets):
                    native=re.fullmatch(r'<@!?(\d+)>',raw)
                    native_role=re.fullmatch(r'<@&(\d+)>',raw)
                    if native_role:
                        role=_guild_role(guild,int(native_role[1]))
                        if role is None:raise ValueError('Role ab server mein verify nahi hua; native @role mention dobara do.')
                        if role.id not in [r.id for r in roles]:roles.append(role)
                        continue
                    member=guild.get_member(int(native[1])) if native else self.resolver(guild,raw)[0]
                    if member is None or member.bot:raise ValueError('Target unclear/bot/left: exact username ya native member mention do.')
                    if member.id not in [t.id for t in targets]:targets.append(member)
        if len(targets)>5:raise ValueError('Maximum 5 targets, ek hi scheduled ping per job.')
        if len(roles)>3:raise ValueError('Maximum 3 role pings, ek hi scheduled ping per job.')
        if roles:
            # A role ping that cannot notify would be a silent failure: refuse now.
            me=getattr(guild,'me',None)
            mention_all=bool(me is not None and getattr(dest.permissions_for(me),'mention_everyone',False))
            if any(not getattr(r,'mentionable',False) and not mention_all for r in roles):
                raise ValueError('Is role ka ping silent ho jayega: role mentionable nahi hai aur mere paas Mention Roles permission nahi. Role settings mein "Allow anyone to @mention this role" on karo ya mujhe permission do, phir dobara set karo.')
        if not body or len(body)>1000:raise ValueError('Exact message 1-1000 characters ka do.')
        # Channel mentions are inert payload (they notify nobody), so "join
        # this <#voice>" stays in the reminder text; only pinging mentions and
        # command markers are refused.
        if re.search(r'@everyone|@here|<@|\[\[(?:tag|search):',body,re.I):
            raise ValueError('Payload mein member/role/everyone mention ya command markers nahi; tags sirf target field se do.')
        core=None
        if reminder:core=body;body=f'Reminder: {body}'
        prefix=' '.join([f'<@{t.id}>' for t in targets]+[f'<@&{r.id}>' for r in roles])
        full=(prefix+'\n' if prefix else '')+body
        draft=dict(guild=guild.id,author=message.author.id,source=message.channel.id,dest=dest.id,targets=[t.id for t in targets],roles=[r.id for r in roles],body=full,due=due,core=core,recurrence=recurrence)
        tm.access(client,draft)
        if self.executor is not None:
            # Execution boundary: the save runs through the shared executor;
            # DONE is recorded only when the read-back verifies the job exists.
            outcome=await self.executor.run('reminder',
                handler=lambda:self.store.create(**draft,now=now,armed=True),
                verify=lambda e:agent_core.verify_saved(self.store,e['id']),
                guild=message.guild.id,channel=message.channel.id,author=message.author.id,
                payload={'recurrence':recurrence},due=due,result_of=lambda e:'saved')
            entry=outcome.result
            if not outcome.ok:
                self._pending_pop(self.key(message),agent_core.FAILED)
                raise OSError('schedule save verify nahi hua')
            self._pending_pop(self.key(message),agent_core.DONE)
        else:
            entry=self.store.create(**draft,now=now,armed=True)
            # Execution truth: claim "saved" only after a read-back confirms the
            # job is actually on disk.
            saved=agent_core.verify_saved(self.store,entry['id'])
            if saved is None:
                self._pending_pop(self.key(message),agent_core.FAILED)
                raise OSError('schedule save verify nahi hua')
            self._pending_pop(self.key(message),agent_core.DONE)
        await self.reply(message,('Repeating reminder saved: '+tm.recurrence_label(entry)+'; next: '+tm.shown(due)+f". Series ID: {entry['id']}. Cancel: Kristy schedule cancel {entry['id']}. Bot time par running chahiye; missed occurrence skip hogi, series chalegi.") if recurrence else 'OK')

    async def handle(self,message,text,client,guard):
        self.client_for_access=client
        # Guard command/header, not literal payload (quotes in a user-authored
        # message are data). No AI or quoted old messages can call this handler.
        header=text.split('|',1)[0]
        if guard(message,header,question=True) is None:return False
        m=re.match(r'^(?:(?:ok+|okay|hey|please|plz|bhai|toh|abhi|kristy)[\s,]+)*(schedule|remind(?:er)?|summon)\b\s*(.*)$',text,re.I|re.S)
        if not m and getattr(message,'reference',None) is not None and REPEAT.search(text):
            return await self.repeat(message,text)
        if not m:
            if INTENT.search(text) or self.pending_item(message):
                return await self.ai_request(message,text,client)
            found=self.helper_item(message,bool(getattr(message,'reference',None)) or self.was_helper_addressed(message))
            if found is not None:
                return await self.ai_request(message,text,client,pkey=found[0],helper=message.author)
            return False
        kind,rest=m[1].lower(),m[2].strip()
        if kind=='reminder':kind='remind'
        # Bounded conversational self-reminder: exact time before ':', literal
        # reminder text after it. No inferred names or rewritten payload.
        natural=re.fullmatch(r'(\d+\s*(?:min(?:ute)?s?|hours?|ghant[ae])\s+baad)\s*:\s*(.+)',rest,re.I|re.S)
        if kind=='remind' and '|' not in rest and natural:
            rest=f'{natural[1]} | here | {natural[2]}' 
        now=time.time();guild=message.guild
        try:
            ctl=re.fullmatch(r'(confirm|cancel)\s+([a-f0-9]{10})',rest,re.I)
            owner=bool(self.owner_id and str(message.author.id)==self.owner_id)
            if ctl:
                action,ident=ctl[1].lower(),ctl[2].lower()
                if action=='confirm':
                    entry=next((e for e in self.store.load() if e['id']==ident and e['guild']==guild.id and e['author']==message.author.id),None)
                    if entry is None:raise ValueError('Apna preview ID nahi mila.')
                    if entry['source']!=message.channel.id:raise ValueError('Confirm wahi channel mein karo jahan preview bana tha.')
                    tm.access(client,entry)
                entry=self.store.control(ident,guild.id,message.author.id,action,now,owner)
                await self.reply(message,f"{entry['id']}: {('future repeating reminders cancelled; current send may already be in flight' if entry.get('cancel_after_send') else ('scheduled' if action=='confirm' else 'cancelled'))}." + (' Bot time par chalni chahiye; 2 minute se zyada late hua toh missed, catch-up ping nahi.' if action=='confirm' else ''))
                return True
            status=re.fullmatch(r'status(?:\s+([a-f0-9]{10}))?',rest,re.I)
            if rest.lower()=='list' or status:
                rows=[]
                for e in self.store.load():
                    if e['guild']!=guild.id or (e['author']!=message.author.id and not owner):continue
                    if status and status[1] and e['id']!=status[1]:continue
                    # Own status remains useful after access/target loss. Omit
                    # destination/body/target identifiers from diagnostics.
                    reason=e.get('reason','legacy_status')
                    if reason not in {'awaiting_confirmation','confirmed','creator_or_owner_cancelled','preview_expired','late_over_2min','access_or_member_check_failed','send_started','send_outcome_unknown','discord_accepted','restart_during_send'}:reason='legacy_status'
                    rows.append(f"{e['id']} | {e['status']} | {tm.shown(e['due'])} | reason={reason}" + (f" | {tm.recurrence_label(e)} | last={e.get('last_status','none')}" if e.get('recurrence') else ''))
                    if status and status[1]:rows.append(tm.diagnostic_summary(e))
                for start in range(0,max(1,len(rows)),8):await self.reply(message,'Worker: '+self.health()+'\n'+('\n'.join(rows[start:start+8]) or 'Is server mein apna saved schedule nahi mila. Normal chat ka okay schedule nahi hota.'))
                return True
            parts=[x.strip() for x in rest.split('|')]
            if len(parts)!=(3 if kind in ('remind','summon') else 4):
                if self.intent is not None:
                    # AI understands first; strict parser only if the AI is unavailable
                    # and the text has no clause (parentheses, tag/ping) it could mangle.
                    fb=lambda:None if re.search(r'[()]|\b(?:tag|ping)',rest,re.I) else self.natural_parts(kind,rest,message)
                    return await self.ai_request(message,text,client,fb,kind)
                found=self.natural_parts(kind,rest,message)
                if found is None:
                    return await self.ai_request(message,text,client)
                parts=found
            await self._commit(message,client,kind,parts,now,getattr(message,'created_at',None))
        except (ValueError,TypeError) as exc:
            await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Schedule data invalid hai; kuch arm nahi hua.')
        except OSError:
            await self.reply(message,'Schedule storage read/write nahi hua; success confirm nahi karungi.')
        return True


_PROMISE=re.compile(r"\b(?:i(?:'ll| will)|main|mai)\b[^.\n]{0,40}\b(?:remind|yaad\s+dila)|\b(?:set|laga\w*|save\w*)\b[^.\n]{0,20}\breminder\b|\breminder\b[^.\n]{0,20}\b(?:set|laga\s?(?:di|diya|dungi)|save)",re.I)
def false_promise(user_text,reply):
    """A chat reply that promises a reminder in a turn where no job was saved."""
    return bool(re.search(r'remind|yaad\s+dila|schedule',user_text or '',re.I) and _PROMISE.search(reply or ''))
NOT_SAVED='Reminder save nahi hua. Time aur kya yaad dilana hai ek line mein likho, jaise: Kristy remind me 10 min baad pani peene'
