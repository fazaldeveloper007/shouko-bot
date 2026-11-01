"""One-shot, preview-confirmed messages. Single process only; no send retries."""
import asyncio
import datetime as dt
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import time
import uuid
import discord

log = logging.getLogger('kristy.schedule')
IST = dt.timezone(dt.timedelta(hours=5, minutes=30), 'IST')
ACTIVE = {'preview', 'scheduled', 'dispatching'}
TERMINAL = {'sent', 'missed', 'cancelled', 'failed', 'uncertain'}


def parse_time(text, anchor):
    """Strict deterministic grammar, relative to original Discord message time."""
    text = text.strip().lower()
    local = anchor.astimezone(IST)
    m = re.fullmatch(r'(\d+)\s*(minute|min|minutes|hour|hours|ghante|ghanta)\s+baad', text)
    if m:
        count = int(m[1]); due = anchor + dt.timedelta(seconds=count * (60 if m[2].startswith('min') else 3600))
    else:
        m = re.fullmatch(r'(aaj|today|kal|tomorrow|\d{4}-\d{2}-\d{2})\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text)
        if not m:
            raise ValueError('Time saaf likho: 10 minute baad / aaj 9:30 PM / kal 8 AM / 2026-11-03 20:30')
        day, hour, minute, ap = m[1], int(m[2]), int(m[3] or 0), m[4]
        if day in ('kal', 'tomorrow') and local.hour < 4:
            raise ValueError('Raat ke 12-4 mein kal unclear hai. YYYY-MM-DD se exact date do.')
        if not ap and not (m[3] and day[0].isdigit()):
            raise ValueError('AM/PM do, ya exact date ke saath 24-hour HH:MM likho.')
        if ap:
            if not 1 <= hour <= 12: raise ValueError('12-hour time mein hour 1-12 hona chahiye.')
            hour = hour % 12 + (12 if ap == 'pm' else 0)
        date = (local + dt.timedelta(days=1 if day in ('kal','tomorrow') else 0)).date() if not day[0].isdigit() else dt.date.fromisoformat(day)
        due = dt.datetime.combine(date, dt.time(hour,minute), IST)
    seconds = (due - anchor).total_seconds()
    if not 60 <= seconds <= 30*86400:
        raise ValueError('Time ab se kam se kam 1 minute aur zyada se zyada 30 din aage ho; past time khud kal nahi karungi.')
    return due.timestamp()


def shown(timestamp):
    return dt.datetime.fromtimestamp(timestamp,IST).strftime('%A, %d %B %Y, %I:%M %p IST (Asia/Kolkata)')


class Store:
    def __init__(self, path): self.path=Path(path)
    def load(self):
        try: data=json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError: return []
        if not isinstance(data,list): raise ValueError('Invalid schedule store')
        for e in data:
            if (not isinstance(e,dict) or e.get('status') not in ACTIVE|TERMINAL
                    or not all(k in e for k in ('id','guild','author','source','dest','targets','body','due','created','expires'))):
                raise ValueError('Invalid schedule entry')
            if (not re.fullmatch(r'[a-f0-9]{10}',str(e['id']))
                    or not all(type(e[k]) is int and e[k]>0 for k in ('guild','author','source','dest'))
                    or not isinstance(e['targets'],list) or len(e['targets'])>5
                    or not all(type(mid) is int and mid>0 for mid in e['targets'])
                    or not isinstance(e['body'],str) or not 1<=len(e['body'])<=1200
                    or not all(type(e[k]) in (int,float) and math.isfinite(e[k]) for k in ('due','created','expires'))):
                raise ValueError('Invalid schedule values')
        return data
    def save(self,data):
        # Never prune live jobs; bounded retained terminal history.
        data=[e for e in data if e['status'] in ACTIVE]+[e for e in data if e['status'] in TERMINAL][-200:]
        self.path.parent.mkdir(parents=True,exist_ok=True); name=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=self.path.parent,delete=False,prefix='.schedule-') as f:
                name=f.name;json.dump(data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
            os.replace(name,self.path)
        finally:
            if name and os.path.exists(name):os.unlink(name)
    def expire(self,data,now):
        for e in data:
            if e['status']=='preview' and now>e['expires']:e['status']='cancelled'
    def create(self, *, guild,author,source,dest,targets,body,due,now):
        data=self.load();self.expire(data,now)
        active=[e for e in data if e['status'] in ACTIVE]
        if len(active)>=1000 or sum(e['guild']==guild for e in active)>=50 or sum(e['author']==author and e['guild']==guild for e in active)>=10:
            raise ValueError('Schedule limit: 10/member, 50/server, 1000 total active previews/jobs.')
        e=dict(id=uuid.uuid4().hex[:10],guild=guild,author=author,source=source,dest=dest,targets=targets,body=body,due=due,created=now,expires=now+120,status='preview')
        data.append(e);self.save(data);return e
    def control(self,ident,guild,author,action,now,owner=False):
        data=self.load();self.expire(data,now)
        e=next((e for e in data if e['id']==ident and e['guild']==guild and (e['author']==author or owner)),None)
        if not e:raise ValueError('Apna schedule ID is server mein nahi mila.')
        if action=='confirm':
            if e['author']!=author or e['status']!='preview':raise ValueError('Sirf creator apna unexpired preview confirm kar sakta hai.')
            if e['due']<=now:raise ValueError('Time nikal gaya. Naya preview banao.')
            e['status']='scheduled'
        elif action=='cancel':
            if e['status'] not in ('preview','scheduled'):raise ValueError('Ye job ab cancel nahi ho sakti; list mein status dekho.')
            e['status']='cancelled'
        self.save(data);return e


def access(client,e):
    guild=client.get_guild(e['guild'])
    if not client.is_ready() or guild is None or guild.unavailable:raise ValueError('Server abhi ready nahi hai.')
    author=guild.get_member(e['author']);me=guild.me;dest=guild.get_channel(e['dest'])
    if not author or not me or not dest or dest.guild.id!=guild.id or dest.type!=discord.ChannelType.text:
        raise ValueError('Creator/destination ab verify nahi hua.')
    for member in (author,me):
        p=dest.permissions_for(member)
        if not (p.view_channel and p.send_messages):raise ValueError('Destination view/send access nahi hai.')
    targets=[guild.get_member(mid) for mid in e['targets']]
    if any(m is None or m.bot for m in targets):raise ValueError('Koi target ab server mein verify nahi hua.')
    return dest,targets


class Worker:
    def __init__(self,client,store,late_seconds=120,connected=lambda:True):
        self.client,self.store,self.late=client,store,late_seconds
        self.task=None;self.halted=False;self.connected=connected
    def start(self):
        if not self.halted and (self.task is None or self.task.done()):self.task=asyncio.create_task(self.run())
    async def close(self):
        if self.task:self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
    async def run(self):
        try:
            # Never replay a pre-crash send with unknown delivery outcome.
            data=self.store.load()
            for e in data:
                if e['status']=='dispatching':e['status']='uncertain'
            self.store.save(data)
            while not self.halted:
                await self.tick()
                await asyncio.sleep(15)
        except asyncio.CancelledError:raise
        except (OSError,ValueError,TypeError):
            self.halted=True;log.error('Schedule worker stopped: state unreadable/unwritable; no send replay')
    async def tick(self,now=None):
        now=time.time() if now is None else now
        data=self.store.load();self.store.expire(data,now)
        self.store.save(data)
        for e in data:
            if e['status']!='scheduled' or e['due']>now:continue
            if now-e['due']>self.late:e['status']='missed';self.store.save(data);continue
            if not self.connected() or not self.client.is_ready():continue
            try:dest,targets=access(self.client,e)
            except ValueError:
                e['status']='failed';self.store.save(data);continue
            e['status']='dispatching';self.store.save(data) # durable BEFORE send
            try:
                sent=await dest.send(e['body'],allowed_mentions=discord.AllowedMentions(users=targets,roles=False,everyone=False,replied_user=False))
            except asyncio.CancelledError:raise
            except Exception:
                e['status']='uncertain' # even network failure may have reached Discord
            else:e['status']='sent';e['sent_message']=sent.id
            # Reload: a user may create/cancel a DIFFERENT job while send awaited.
            # Never overwrite that concurrent action with this old snapshot.
            fresh=self.store.load()
            for ix,latest in enumerate(fresh):
                if latest['id']==e['id']:fresh[ix]=e;break
            try:self.store.save(fresh)
            except (OSError,ValueError,TypeError):self.halted=True;raise
            # Subsequent jobs must be loaded fresh to observe cancellation.
            return
