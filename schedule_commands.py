"""Strict preview-confirmed timed messages; content after | is literal data."""
import re
import time

INTENT = re.compile(r"\b(remind(?:er)?|schedule|timer|yaad\s+dila(?:na|do|dena)?|reminder\s+laga)\b|\b\d+\s*(?:min(?:ute)?s?|hours?|ghant[ae])\s+baad\b.*\b(?:bhej|bol|bula|tag|ping|message)|\b(?:bhej\w*|bol\w*|bula\w*|tag\w*|ping\w*|yaad\s+dila\w*|message)\b[^\n|]*\b\d+\s*(?:min(?:ute)?s?|hours?|ghant[ae])\s+baad\b|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b.*\b(?:bhej|bol|bula|tag|ping|message)", re.I)
import discord
import timed_messages as tm

HELP = ('Schedule format:\nKristy schedule 10 minute baad | here | Mayank, Naiyo | Exact message\n'
        'Kristy schedule kal 8 PM | #text-channel | @member | VC join kar lo\n'
        'Kristy remind 10 minute baad | here | apna reminder\n'
        'Kristy summon kal 8 PM | #text-channel | Mayank, Naiyo\n'
        'Ya seedha aise bhi: Kristy remind me after 10 minutes to drink water\n'
        'Kristy schedule confirm ID / schedule list / schedule cancel ID\n'
        'Doosre channel mein bhejna ho tabhi native #text-channel mention chahiye; warna request wale channel mein jayega. Targets comma/aur se separate. Preview ke bina kuch arm nahi hoga.')

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


class Commands:
    def __init__(self,store,resolver,owner_id=''):
        self.store,self.resolver,self.owner_id=store,resolver,owner_id
        self.pending={};self.health=lambda:'unknown'
    def key(self,message):return message.guild.id,message.channel.id,message.author.id
    def followup(self,message,text):
        now=time.monotonic();self.pending={k:v for k,v in self.pending.items() if v[0]>now}
        item=self.pending.get(self.key(message))
        ref=getattr(message,'reference',None)
        return bool(item and (ref is None or getattr(ref,'message_id',None)==item[1]) and re.fullmatch(r'(?:(?:haan?|ha|okay|ok|yes)\s+)?(?:haan?|ha|okay|ok|confirm|yes|isi channel mein(?: dena)?|same channel|yahi(?: channel)?)[ .!]*',text,re.I))
    async def reply(self,message,text):
        return await message.reply(text,mention_author=False,allowed_mentions=discord.AllowedMentions.none())
    async def clarify(self,message):
        sent=await self.reply(message,'Abhi schedule save/active nahi hua. Exact time, destination, targets aur message chahiye. Neeche format se preview banao; sirf okay/same channel bolne se job arm nahi hota.\n'+HELP)
        if len(self.pending)>=1000:self.pending.pop(next(iter(self.pending)))
        self.pending[self.key(message)]=(time.monotonic()+120,getattr(sent,'id',None))

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
        if kind=='remind' and mentions:return None  # multi-target: use strict format
        text=re.sub(r'<@!?\d+>',' ',text)
        text=_TAG_WORDS.sub(' ',text)
        text=_HERE_WORDS.sub(' ',text)
        self_target=bool(re.search(r'\b(?:me|mujhe|mujhko|mujko|humhe|humko)\b',text,re.I))
        body=_strip_lead(text).strip(' .,!')
        if body and not any(t.strip('.,!').casefold() not in _COMMAND_FILLER for t in body.split()):body=''
        if kind=='remind':
            return [when,dest,body] if body else None
        targets=list(dict.fromkeys(mentions))
        if self_target and str(message.author.id) not in targets:targets.insert(0,str(message.author.id))
        if kind=='summon':
            return [when,dest,', '.join(f'<@{t}>' for t in targets)] if targets else None
        if not body:return None
        return [when,dest,', '.join(f'<@{t}>' for t in targets) or 'none',body]

    async def handle(self,message,text,client,guard):
        # Guard command/header, not literal payload (quotes in a user-authored
        # message are data). No AI or quoted old messages can call this handler.
        header=text.split('|',1)[0]
        if guard(message,header,question=True) is None:return False
        m=re.match(r'^(?:(?:ok+|okay|hey|please|plz|bhai|toh|abhi|kristy)[\s,]+)*(schedule|remind(?:er)?|summon)\b\s*(.*)$',text,re.I|re.S)
        if not m:
            if INTENT.search(text) or self.followup(message,text):
                await self.clarify(message);return True
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
                found=self.natural_parts(kind,rest,message)
                if found is None:
                    await self.clarify(message);return True
                parts=found
            when,destination=parts[:2]
            anchor=getattr(message,'created_at',None)
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
            full=(' '.join(f'<@{t.id}>' for t in targets)+'\n' if targets else '')+body
            draft=dict(guild=guild.id,author=message.author.id,source=message.channel.id,dest=dest.id,targets=[t.id for t in targets],body=full,due=due)
            tm.access(client,draft)
            entry=self.store.create(**draft,now=now)
            self.pending.pop(self.key(message),None)
            await self.reply(message,f"Preview {entry['id']} (abhi armed nahi):\n{tm.shown(due)}\nDestination: {discord.utils.escape_markdown(dest.name)} (ID {dest.id})\nExact outgoing text:\n{full}\n\nIsi channel mein 2 minute ke andar: Kristy schedule confirm {entry['id']}\nBot chalni chahiye. 15-second check cadence; 2 minute se zyada late -> missed, no catch-up. 10 jobs/member, 50/server, max30days. Unknown send outcome par automatic retry nahi, duplicate ping se bachne ke liye.")
        except (ValueError,TypeError) as exc:
            await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Schedule data invalid hai; kuch arm nahi hua.')
        except OSError:
            await self.reply(message,'Schedule storage read/write nahi hua; success confirm nahi karungi.')
        return True
