"""Strict preview-confirmed timed messages; content after | is literal data."""
import re
import time
import discord
import timed_messages as tm

HELP = ('Schedule format:\nKristy schedule 10 minute baad | here | Mayank, Naiyo | Exact message\n'
        'Kristy schedule kal 8 PM | #text-channel | @member | VC join kar lo\n'
        'Kristy remind 10 minute baad | here | apna reminder\n'
        'Kristy summon kal 8 PM | #text-channel | Mayank, Naiyo\n'
        'Kristy schedule confirm ID / schedule list / schedule cancel ID\n'
        'Native #text-channel mention select karo; targets comma/aur se separate. Preview ke bina kuch arm nahi hoga.')


class Commands:
    def __init__(self,store,resolver,owner_id=''):
        self.store,self.resolver,self.owner_id=store,resolver,owner_id
    async def reply(self,message,text):
        await message.reply(text,mention_author=False,allowed_mentions=discord.AllowedMentions.none())
    async def handle(self,message,text,client,guard):
        # Guard command/header, not literal payload (quotes in a user-authored
        # message are data). No AI or quoted old messages can call this handler.
        header=text.split('|',1)[0]
        if guard(message,header,question=True) is None:return False
        m=re.match(r'^(schedule|remind|summon)\b\s*(.*)$',text,re.I|re.S)
        if not m:return False
        kind,rest=m[1].lower(),m[2].strip()
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
            if rest.lower()=='list':
                rows=[]
                for e in self.store.load():
                    if e['guild']!=guild.id or (e['author']!=message.author.id and not owner):continue
                    # Never reveal jobs in hidden destinations, including owner list.
                    try:dest,_=tm.access(client,e)
                    except ValueError:continue
                    if not dest.permissions_for(message.author).view_channel:continue
                    rows.append(f"{e['id']} | {e['status']} | {tm.shown(e['due'])} | channel {e['dest']}")
                for start in range(0,max(1,len(rows)),8):await self.reply(message,'\n'.join(rows[start:start+8]) or 'Koi visible schedule nahi mila.')
                return True
            parts=[x.strip() for x in rest.split('|')]
            if len(parts)!=(3 if kind in ('remind','summon') else 4):
                await self.reply(message,HELP);return True
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
            await self.reply(message,f"Preview {entry['id']} (abhi armed nahi):\n{tm.shown(due)}\nDestination: {discord.utils.escape_markdown(dest.name)} (ID {dest.id})\nExact outgoing text:\n{full}\n\nIsi channel mein 2 minute ke andar: Kristy schedule confirm {entry['id']}\nBot chalni chahiye. 15-second check cadence; 2 minute se zyada late -> missed, no catch-up. 10 jobs/member, 50/server, max30days. Unknown send outcome par automatic retry nahi, duplicate ping se bachne ke liye.")
        except (ValueError,TypeError) as exc:
            await self.reply(message,str(exc) if isinstance(exc,ValueError) else 'Schedule data invalid hai; kuch arm nahi hua.')
        except OSError:
            await self.reply(message,'Schedule storage read/write nahi hua; success confirm nahi karungi.')
        return True
