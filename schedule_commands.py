"""Timed messages saved on a clear request (no confirm step); content after | is literal data."""
import re
import time

INTENT = re.compile(r"\b(remind(?:er)?|schedule|timer|yaad\s+dila(?:na|do|dena)?|reminder\s+laga)\b|\b\d+\s*(?:min(?:ute)?s?|hours?|ghant[ae])\s+baad\b.*\b(?:bhej|bol|bula|tag|ping|message)|\b(?:bhej\w*|bol\w*|bula\w*|tag\w*|ping\w*|yaad\s+dila\w*|message)\b[^\n|]*\b\d+\s*(?:min(?:ute)?s?|hours?|ghant[ae])\s+baad\b|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b.*\b(?:bhej|bol|bula|tag|ping|message)", re.I)
import discord
import timed_messages as tm

HELP = ('Kristy remind me 10 min baad pani peene\n'
        'Kristy remind me tagging Mayank 10 min baad to watch anime\n'
        'Kristy schedule kal 8 PM | #text-channel | Mayank, Naiyo | message\n'
        'Kisi message ko reply karke: 5 min baad same message dena\n'
        'schedule list / schedule cancel ID')

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



_CLOCK=re.compile(r"\b(?:(aaj|today|kal|tomorrow)\s+)?(?:at\s+|@\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",re.I)
_CLOCK24=re.compile(r"(?<![\d:-])(?:(\d{4}-\d{2}-\d{2})\s+|(?:at\s+)?)([01]?\d|2[0-3]):([0-5]\d)\b(?!\s*(?:am|pm))",re.I)
_REL=re.compile(r"\b(?:after|in)\s+(\d+)\s*(min(?:ute)?s?|hours?|hrs?)\b|\b(\d+)\s*(min(?:ute)?s?|hours?|hrs?|ghant[ae])\s+baad\b",re.I)
_APPROX=re.compile(r"(?:almost|around|approx\w*|about|nearly|lagbhag|takriban|roughly|~)\W*$",re.I)

def _tagged_in(text):
    """Names the user wrote after a tag/ping word ('tag Mayank, Naiyo')."""
    out=[]
    for m in _TAG_NAME.finditer(text):
        out.append(m[1].strip('.,!'));end=m.end()
        while True:
            n=_NAME_LIST.match(text,end)
            if not n:break
            out.append(n[1].strip('.,!'));end=n.end()
    return out

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

class Commands:
    def __init__(self,store,resolver,owner_id=''):
        self.store,self.resolver,self.owner_id=store,resolver,owner_id
        self.pending={};self.intent=None;self.health=lambda:'unknown';self.bot_id=None;self.client_for_access=None
    def key(self,message):return message.guild.id,message.channel.id,message.author.id
    def pending_item(self,message):
        now=time.monotonic();self.pending={k:v for k,v in self.pending.items() if v[0]>now}
        item=self.pending.get(self.key(message))
        ref=getattr(message,'reference',None)
        if item and ref is not None and getattr(ref,'message_id',None)!=item[1]:return None
        return item
    def followup(self,message,text,addressed=True):
        """A reply to our own clarification (same guild/channel/author, within
        2 minutes). Anything said then goes to the intent step with the original
        request; the model may say it is unrelated and normal chat continues."""
        item=self.pending_item(message)
        if not item:return False
        ref=getattr(message,'reference',None)
        return ref is not None or bool(addressed)
    async def reply(self,message,text):
        return await message.reply(text,mention_author=False,allowed_mentions=discord.AllowedMentions.none())
    async def clarify(self,message,text='Kab aur kya? Jaise: Kristy remind me 10 min baad pani peene',request=None,anchor=None):
        """Ask a short question and remember the ORIGINAL request text and its
        message time, so the user's answer completes that request."""
        sent=await self.reply(message,text)
        if len(self.pending)>=1000:self.pending.pop(next(iter(self.pending)))
        self.pending[self.key(message)]=(time.monotonic()+120,getattr(sent,'id',None),request,anchor or getattr(message,'created_at',None))

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

    async def ai_request(self,message,text,client,fallback=None,kind='remind'):
        """The model only UNDERSTANDS the request (structured intent). Every
        effect is decided here: time via tm.parse_time against the ORIGINAL
        message time, targets resolved to real members, same channel unless a
        native #channel mention is in the user's own text, and nothing is
        confirmed to the user before the job is on disk."""
        self.client_for_access=client
        item=self.pending_item(message)
        orig,anchor=(item[2],item[3]) if item else (None,None)
        anchor=anchor or getattr(message,'created_at',None)
        request=(orig+'\n'+text) if orig else text
        if anchor is None:
            await self.reply(message,'Original message ka time verify nahi hua; kuch save nahi hua.');return True
        intent=None
        if self.intent is not None:
            try:intent=await self.intent(orig,text,anchor)
            except Exception:intent=None
        if self.intent is None:
            await self.clarify(message,request=request,anchor=anchor);return True
        if not isinstance(intent,dict) or intent.get('action') not in ('remind','need_info','none'):
            parts=fallback() if fallback else None
            if parts:
                try:
                    await self._commit(message,client,kind,parts,time.time(),getattr(message,'created_at',None))
                    self.pending.pop(self.key(message),None)
                except (ValueError,TypeError) as exc:
                    await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Schedule data invalid hai; kuch save nahi hua.')
                except OSError:
                    await self.reply(message,'Schedule storage read/write nahi hua; success confirm nahi karungi.')
                return True
            await self.clarify(message,'Abhi samajh nahi paayi, reminder save nahi hua. Time aur kya yaad dilana hai ek line mein likho.',request,anchor);return True
        action=intent['action']
        if action=='none':return False
        if action=='need_info':
            q=intent.get('question')
            q=' '.join(q.split())[:150] if isinstance(q,str) and q.strip() else 'Kab aur kya?'
            q=re.sub(r'<[@#]|@(?:everyone|here)','',q)
            await self.clarify(message,q,request,anchor);return True
        try:
            when,task=intent.get('when'),intent.get('task')
            names=intent.get('tag_names') or []
            if not isinstance(when,str) or not isinstance(task,str) or not isinstance(names,list) or len(names)>5:raise TypeError
            when=' '.join(when.split());task=' '.join(task.split()).strip(' .,!')
            if not task or not when or len(when)>40 or len(task)>300:
                await self.clarify(message,'Kab aur kya? Time aur reminder ka kaam ek line mein likho.',request,anchor);return True
            # The task must be the user's own words, not a model invention.
            words=[w for w in re.findall(r"[\w']+",task.casefold()) if len(w)>=3]
            low=request.casefold()
            if not words or sum(w in low for w in words)*2<len(words):
                await self.clarify(message,'Kya yaad dilana hai? Ek line mein likho.',request,anchor);return True
            # The time comes from the user's words (clock or duration), computed
            # here; the model's `when` only has to AGREE with it.
            sources=[(orig,anchor)] if orig else []
            sources.append((text,getattr(message,'created_at',None) or anchor))
            chosen,problem=pick_time(when,sources)
            if chosen is None:
                await self.clarify(message,problem,request,anchor);return True
            when,when_anchor=chosen
            dests=re.findall(r'<#(\d+)>',request)
            if len(dests)>1:raise ValueError('Ek hi destination channel do.')
            if dests and not re.search(rf'(?:\b(?:in|to|on|at)\s+<#{dests[0]}>|<#{dests[0]}>\s*(?:mein|me|pe|par|channel)\b|\bchannel\s+<#{dests[0]}>)',request,re.I):
                await self.clarify(message,'Kis channel mein bhejna hai? "<#channel> mein" likho, ya bina channel ke yahin bhejti hoon.',request,anchor);return True
            dest=f'<#{dests[0]}>' if dests else 'here'
            targets=[str(message.author.id)]
            mentioned=re.findall(r'<@!?(\d+)>',request)
            for m_id in mentioned:
                if not re.search(rf'\b(?:tag(?:ging)?|ping(?:ing)?)\s+(?:[\w.\-]+\s*(?:,|and|aur)\s*)*<@!?{m_id}>',request,re.I):
                    await self.clarify(message,'Kisko tag karna hai? "tag @naam" likho.',request,anchor);return True
            targets+=mentioned
            for name in names:
                if not isinstance(name,str) or not name.strip() or len(name)>40:raise TypeError
                clean=name.strip().lstrip('@')
                # a tagged person must be named after a tag/ping word in the user's text
                if not any(clean.casefold()==n.casefold() for n in _tagged_in(request)):
                    await self.clarify(message,f'"{clean}" ko tag karna hai? "tag {clean}" likho.',request,anchor);return True
                member=self.resolver(message.guild,clean)[0]
                if member is None or member.bot:raise ValueError(f'"{name}" member nahi mila; exact username ya @mention do.')
                targets.append(str(member.id))
            targets=list(dict.fromkeys(targets))
            # the model cannot drop the author's tag unless the user said so
            tag_author=not (intent.get('tag_author') is False and re.search(r"(?:don't|do not|dont|mat|without|no)\s+tag|tag\s+(?:mat|nahi|nhi)",request,re.I))
            if len(targets)==1 and tag_author:
                parts=[when,dest,task];kind='remind'
            else:
                parts=[when,dest,', '.join(f'<@{t}>' for t in targets),task];kind='schedule'
            await self._commit(message,client,kind,parts,time.time(),when_anchor)
            self.pending.pop(self.key(message),None)
        except (ValueError,TypeError) as exc:
            self.pending.pop(self.key(message),None)
            await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Request data invalid hai; kuch save nahi hua.')
        except OSError:
            await self.reply(message,'Schedule storage read/write nahi hua; success confirm nahi karungi.')
        return True

    async def _commit(self,message,client,kind,parts,now,anchor):
        guild=message.guild
        reminder=kind=='remind'
        if reminder and len(parts)==4:kind='schedule'
        when,destination=parts[:2]
        if anchor is None:raise ValueError('Original message ka time verify nahi hua.')
        due=tm.parse_time(when,anchor)
        if due<=now:raise ValueError('Time nikal gaya; naya time do.')
        if destination.casefold()=='here':dest=message.channel
        elif re.fullmatch(r'<#\d+>',destination):dest=guild.get_channel(int(destination[2:-1]))
        else:raise ValueError('Destination here ya ek native #text-channel mention hona chahiye.')
        if dest is None:raise ValueError('Destination is server mein verify nahi hua.')
        targets=[]
        if kind=='remind':targets=[message.author];body=parts[2]
        else:
            raw_targets=parts[2]
            body='VC join kar lo, creator ne is time par bulaya hai.' if kind=='summon' else parts[3]
            if raw_targets.casefold() not in ('none','koi nahi'):
                for raw in re.split(r'\s*,\s*|\s+aur\s+|\s+and\s+',raw_targets):
                    native=re.fullmatch(r'<@!?(\d+)>',raw)
                    member=guild.get_member(int(native[1])) if native else self.resolver(guild,raw)[0]
                    if member is None or member.bot:raise ValueError('Target unclear/bot/left: exact username ya native member mention do.')
                    if member.id not in [t.id for t in targets]:targets.append(member)
        if len(targets)>5:raise ValueError('Maximum 5 targets, ek hi scheduled ping per job.')
        if not body or len(body)>1000:raise ValueError('Exact message 1-1000 characters ka do.')
        if re.search(r'@everyone|@here|<@|<#|\[\[(?:tag|search):',body,re.I):
            raise ValueError('Payload mein mention/command markers nahi; tags sirf target field se do.')
        core=None
        if reminder:core=body;body=f'Reminder: {body}'
        full=(' '.join(f'<@{t.id}>' for t in targets)+'\n' if targets else '')+body
        draft=dict(guild=guild.id,author=message.author.id,source=message.channel.id,dest=dest.id,targets=[t.id for t in targets],body=full,due=due,core=core)
        tm.access(client,draft)
        self.store.create(**draft,now=now,armed=True)
        self.pending.pop(self.key(message),None)
        await self.reply(message,'OK')


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
                await self.reply(message,f"{entry['id']}: {'scheduled' if action=='confirm' else 'cancelled'}." + (' Bot time par chalni chahiye; 2 minute se zyada late hua toh missed, catch-up ping nahi.' if action=='confirm' else ''))
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
                    rows.append(f"{e['id']} | {e['status']} | {tm.shown(e['due'])} | reason={reason}")
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
