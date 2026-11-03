import asyncio, datetime as dt, tempfile, pathlib, unittest
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import discord
import schedule_commands as sc
import timed_messages as tm

IST=tm.IST
def guard(*a,**k):return 'ok'

class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=tm.Store(pathlib.Path(self.tmp.name)/'s.json')
        self.guild=S(id=10,unavailable=False,me=S(id=99,bot=True),get_member=lambda i:None,get_channel=lambda c:None)
        self.author=S(id=1,bot=False,display_name='Fazal')
        self.mayank=S(id=2,bot=True and False,display_name='Mayank')
        self.dest=S(id=20,guild=self.guild,type=discord.ChannelType.text,name='chat',send=AsyncMock())
        self.dest.permissions_for=lambda m:S(view_channel=True,send_messages=True)
        self.guild.get_channel=lambda c:self.dest if c==20 else None
        self.guild.get_member=lambda i:{1:self.author,2:self.mayank}.get(i)
        self.client=S(user=S(id=99),get_guild=lambda i:self.guild,is_ready=lambda:True)
        self.calls=[]
        self.cmd=sc.Commands(self.store,lambda g,n:(self.mayank,False) if n.lower()=='mayank' else (None,True))
        self.cmd.bot_id=lambda:99
    def msg(self,when,author=None,channel=None,ref=None):
        return S(guild=self.guild,author=author or self.author,channel=channel or self.dest,created_at=when,reply=AsyncMock(return_value=S(id=700)),reference=ref)
    def ai(self,*answers):
        it=iter(answers)
        async def f(orig,follow,anchor):
            self.calls.append((orig,follow,anchor));a=next(it)
            if isinstance(a,Exception):raise a
            return a
        self.cmd.intent=f
    async def run_msg(self,m,text):return await self.cmd.handle(m,text,self.client,guard)

REQ="OK, Kristy see you, currently I am working with the hand hold works so can you please remind me at 3:30 pm (almost after 19 minute) for lunch break, do also tag me so I get notification"

class Intent(Base):
    async def test_nov3_message_saves_exact_time_and_says_ok(self):
        now=dt.datetime.now(IST);self.assertTrue(True)
        # use a message time 19 min before a clock time later today (stay within same day)
        anchor=dt.datetime.now(dt.timezone.utc)
        target=(anchor.astimezone(IST)+dt.timedelta(minutes=19))
        hh=target.strftime('%I').lstrip('0');mm=target.strftime('%M');ap=target.strftime('%p')
        req=REQ.replace('3:30 pm',f'{hh}:{mm} {ap.lower()}')
        self.ai({'action':'remind','when':f'aaj {hh}:{mm} {ap}','task':'lunch break','tag_names':[],'tag_author':True})
        m=self.msg(anchor)
        self.assertTrue(await self.run_msg(m,req))
        e=self.store.load()[0]
        self.assertEqual((e['status'],e['targets'],e['core']),('scheduled',[1],'lunch break'))
        self.assertAlmostEqual(e['due'],target.replace(second=0,microsecond=0).timestamp(),delta=1)
        self.assertEqual(m.reply.call_args.args[0],'OK')
    async def test_ok_only_after_save_when_storage_fails(self):
        anchor=dt.datetime.now(dt.timezone.utc)
        self.ai({'action':'remind','when':'10 minute baad','task':'pani','tag_names':[]})
        self.store.create=lambda **k:(_ for _ in ()).throw(OSError())
        m=self.msg(anchor);await self.run_msg(m,'bhai ek reminder chahiye 10 minute baad pani peene ka')
        self.assertNotEqual(m.reply.call_args.args[0],'OK');self.assertEqual(self.store.load(),[])
    async def test_ai_outage_never_saves_or_promises(self):
        self.ai(RuntimeError('down'))
        m=self.msg(dt.datetime.now(dt.timezone.utc))
        self.assertTrue(await self.run_msg(m,'can you remind me in 10 minutes for tea'))
        self.assertEqual(self.store.load(),[]);self.assertIn('save nahi hua',m.reply.call_args.args[0])
    async def test_invalid_output_clarifies(self):
        for bad in ('text',None,{'action':'delete_all'},{'action':'remind','when':'tomorrow maybe','task':'x','tag_names':[]},{'action':'remind','when':'10 minute baad','task':'x','tag_names':'Mayank'}):
            self.ai(bad);m=self.msg(dt.datetime.now(dt.timezone.utc))
            await self.run_msg(m,'please remind me later about tea')
            self.assertEqual(self.store.load(),[],bad)
            self.assertTrue(m.reply.called,bad)
    async def test_invented_time_rejected(self):
        self.ai({'action':'remind','when':'aaj 9:00 PM','task':'tea','tag_names':[]})
        m=self.msg(dt.datetime.now(dt.timezone.utc));await self.run_msg(m,'remind me for tea soon please')
        self.assertEqual(self.store.load(),[])
    async def test_not_a_reminder_continues_to_normal_chat(self):
        self.ai({'action':'none'})
        self.assertFalse(await self.run_msg(self.msg(dt.datetime.now(dt.timezone.utc)),'what is the schedule of the anime tonight'))
        self.assertEqual(self.store.load(),[])
    async def test_followup_resolves_original_with_original_time(self):
        t0=dt.datetime.now(dt.timezone.utc)
        self.ai({'action':'need_info','question':'Kya yaad dilana hai?'},{'action':'remind','when':'10 minute baad','task':'tea','tag_names':[]})
        m1=self.msg(t0);await self.run_msg(m1,'remind me in 10 minutes');self.assertEqual(self.store.load(),[])
        self.assertEqual(m1.reply.call_args.args[0],'Kya yaad dilana hai?')
        m2=self.msg(t0+dt.timedelta(seconds=90),ref=S(message_id=700))
        self.assertTrue(self.cmd.followup(m2,'for tea',True))
        await run(self,m2,'for tea')
        e=self.store.load()[0];self.assertEqual(e['core'],'tea')
        self.assertAlmostEqual(e['due'],t0.timestamp()+600,delta=1)  # from ORIGINAL, not the followup
        self.assertEqual(self.calls[1][0],'remind me in 10 minutes');self.assertEqual(self.calls[1][1],'for tea')
        self.assertIsNone(self.cmd.pending_item(m2))
    async def test_no_cross_user_or_channel_bleed(self):
        t0=dt.datetime.now(dt.timezone.utc)
        self.ai({'action':'need_info','question':'Kab?'},{'action':'none'},{'action':'none'})
        await self.run_msg(self.msg(t0),'remind me to drink water')
        other=S(id=3,bot=False,display_name='O')
        mo=self.msg(t0,author=other);self.assertFalse(self.cmd.followup(mo,'10 minute baad',True))
        ch2=S(id=21,guild=self.guild);mc=self.msg(t0,channel=ch2);self.assertFalse(self.cmd.followup(mc,'10 minute baad',True))
        self.assertFalse(await self.run_msg(mo,'hello there'))
        self.assertEqual(len(self.calls),1)
    async def test_followup_replying_to_other_message_not_matched(self):
        t0=dt.datetime.now(dt.timezone.utc);self.ai({'action':'need_info','question':'Kab?'})
        await self.run_msg(self.msg(t0),'remind me to drink water')
        self.assertFalse(self.cmd.followup(self.msg(t0,ref=S(message_id=555)),'ok',True))
    async def test_expired_followup_ignored(self):
        t0=dt.datetime.now(dt.timezone.utc);self.ai({'action':'need_info','question':'Kab?'})
        await self.run_msg(self.msg(t0),'remind me to drink water')
        k=self.cmd.key(self.msg(t0));v=self.cmd.pending[k];self.cmd.pending[k]=(0,)+v[1:]
        self.assertFalse(self.cmd.followup(self.msg(t0),'ok',True))
    async def test_tag_other_member_and_unknown_name(self):
        t0=dt.datetime.now(dt.timezone.utc)
        self.ai({'action':'remind','when':'5 minute baad','task':'standup','tag_names':['Mayank']})
        await self.run_msg(self.msg(t0),'remind me and tag Mayank in 5 minute for standup')
        self.assertEqual(self.store.load()[0]['targets'],[1,2])
        self.ai({'action':'remind','when':'5 minute baad','task':'standup','tag_names':['Nobody']})
        m=self.msg(t0);await self.run_msg(m,'remind me and tag Nobody in 5 minute for standup')
        self.assertEqual(len(self.store.load()),1);self.assertIn('member nahi mila',m.reply.call_args.args[0])
    async def test_model_cannot_choose_channel_or_inject_mentions(self):
        t0=dt.datetime.now(dt.timezone.utc)
        self.ai({'action':'remind','when':'5 minute baad','task':'hi @everyone <@2>','tag_names':[],'dest':'<#99>'})
        m=self.msg(t0);await self.run_msg(m,'remind me in 5 minute to say hi')
        self.assertEqual(self.store.load(),[])
    async def test_past_time_not_saved(self):
        t0=dt.datetime.now(dt.timezone.utc)
        self.ai({'action':'remind','when':'aaj 1:00 AM','task':'x','tag_names':[]})
        m=self.msg(t0.astimezone(IST).replace(hour=14));await self.run_msg(m,'remind me at 1:00 am for x')
        self.assertEqual(self.store.load(),[])
    async def test_regex_path_still_deterministic_without_ai(self):
        m=self.msg(dt.datetime.now(dt.timezone.utc));self.cmd.intent=None
        await self.run_msg(m,'remind me here after 3 minute to play genshin');self.assertEqual(self.store.load()[0]['core'],'play genshin')

class Grounding(Base):
    """Mocked extraction only: these test the code's checks, not model quality."""
    def at(self,mins=20):
        t=dt.datetime.now(dt.timezone.utc).astimezone(IST).replace(second=0,microsecond=0)+dt.timedelta(minutes=mins)
        return t,f"{t.strftime('%I').lstrip('0')}:{t.strftime('%M')} {t.strftime('%p')}"
    async def attempt(self,text,intent,when=None):
        self.cmd.pending.clear();self.ai(intent);m=self.msg(when or dt.datetime.now(dt.timezone.utc));await self.run_msg(m,text);return m
    async def test_clock_normalisations_equal(self):
        t,c=self.at()
        for w in (f'aaj {c}',f'{t.date().isoformat()} {t.strftime("%H:%M")}'):
            self.store.save([])
            await self.attempt(f'remind me at {c.lower()} for tea',{'action':'remind','when':w,'task':'tea','tag_names':[]})
            self.assertEqual(len(self.store.load()),1,w)
            self.assertAlmostEqual(self.store.load()[0]['due'],t.timestamp(),delta=1)
    async def test_explicit_clock_beats_parenthetical_approx(self):
        t,c=self.at(20)
        await self.attempt(f'remind me at {c} (almost after 19 minute) for tea',{'action':'remind','when':'19 minute baad','task':'tea','tag_names':[]})
        self.assertEqual(self.store.load(),[])  # model chose the approximation: refused
        await self.attempt(f'remind me at {c} (almost after 19 minute) for tea',{'action':'remind','when':f'aaj {c}','task':'tea','tag_names':[]})
        self.assertAlmostEqual(self.store.load()[0]['due'],t.timestamp(),delta=1)
    async def test_conflicting_exact_times_ask(self):
        t,c=self.at(20)
        m=await self.attempt(f'remind me at {c} and after 5 minute for tea',{'action':'remind','when':'5 minute baad','task':'tea','tag_names':[]})
        self.assertEqual(self.store.load(),[]);self.assertIn('alag time',m.reply.call_args.args[0])
    async def test_recombined_digits_do_not_pass(self):
        t,c=self.at(20)
        await self.attempt(f'remind me at {c} to call 2 friends',{'action':'remind','when':'2 minute baad','task':'call friends','tag_names':[]})
        self.assertEqual(self.store.load(),[])
        await self.attempt('remind me at 3 to call 30 friends',{'action':'remind','when':'aaj 3:30 PM','task':'call friends','tag_names':[]})
        self.assertEqual(self.store.load(),[])
    async def test_followup_time_counts_from_followup_message(self):
        t0=dt.datetime.now(dt.timezone.utc)
        self.ai({'action':'need_info','question':'Kab?'},{'action':'remind','when':'10 minute baad','task':'tea','tag_names':[]})
        await self.run_msg(self.msg(t0),'remind me to drink tea')
        t1=t0+dt.timedelta(seconds=100)
        await self.run_msg(self.msg(t1,ref=S(message_id=700)),'in 10 minutes')
        self.assertAlmostEqual(self.store.load()[0]['due'],t1.timestamp()+600,delta=1)
    async def test_tag_names_must_be_user_words(self):
        t0=dt.datetime.now(dt.timezone.utc)
        await self.attempt('remind me in 5 minute about Mayank birthday cake',{'action':'remind','when':'5 minute baad','task':'Mayank birthday cake','tag_names':['Mayank']})
        self.assertEqual(self.store.load(),[])
        await self.attempt('remind me in 5 minute tag Mayank for standup',{'action':'remind','when':'5 minute baad','task':'standup','tag_names':['Mayank']})
        self.assertEqual(self.store.load()[0]['targets'],[1,2])
    async def test_model_cannot_remove_author_tag(self):
        await self.attempt('remind me in 5 minute for standup',{'action':'remind','when':'5 minute baad','task':'standup','tag_names':[],'tag_author':False})
        self.assertEqual(self.store.load()[0]['targets'],[1])
        self.store.save([])
        await self.attempt("remind me in 5 minute for standup, don't tag me",{'action':'remind','when':'5 minute baad','task':'standup','tag_names':[],'tag_author':False})
    async def test_channel_mention_only_with_destination_words(self):
        self.guild.get_channel=lambda c:self.dest if c in (20,21) else None
        await self.attempt('remind me in 5 minute to check <#21> rules',{'action':'remind','when':'5 minute baad','task':'check rules','tag_names':[]})
        self.assertEqual(self.store.load(),[])
        await self.attempt('remind me in 5 minute in <#20> to drink water',{'action':'remind','when':'5 minute baad','task':'drink water','tag_names':[]})
        self.assertEqual(self.store.load()[0]['dest'],20)
    async def test_invented_task_and_casual_chat(self):
        await self.attempt('remind me in 5 minute please',{'action':'remind','when':'5 minute baad','task':'submit the tax return','tag_names':[]})
        self.assertEqual(self.store.load(),[])
        self.cmd.pending.clear();self.calls=[];self.ai({'action':'remind','when':'5 minute baad','task':'x','tag_names':[]})
        self.assertFalse(await self.run_msg(self.msg(dt.datetime.now(dt.timezone.utc)),'lol that anime in 5 minutes was wild'))
        self.assertEqual(self.calls,[])  # no keyword: the model is never asked

async def run(t,m,text):return await t.cmd.handle(m,text,t.client,guard)

class Guard(unittest.TestCase):
    def test_false_promise_replaced(self):
        self.assertTrue(sc.false_promise('remind me at 3:30','Done! I\'ll remind you at 3:30 for lunch'))
        self.assertTrue(sc.false_promise('remind me','Reminder set for 3:30 pm'))
        self.assertFalse(sc.false_promise('hello','I\'ll remind you later'))
        self.assertFalse(sc.false_promise('what is a reminder','a reminder is a note'))

class Outage(Base):
    async def test_outage_simple_strict_form_still_saves_but_clause_text_does_not(self):
        self.ai(RuntimeError('down'))
        await self.run_msg(self.msg(dt.datetime.now(dt.timezone.utc)),'remind me 10 minute baad pani peene')
        self.assertEqual(self.store.load()[0]['core'],'pani peene')
        self.ai(RuntimeError('down'));m=self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m,'remind me at 11:00 pm (almost after 19 minute) for lunch, also tag me')
        self.assertEqual(len(self.store.load()),1);self.assertIn('save nahi hua',m.reply.call_args.args[0])
