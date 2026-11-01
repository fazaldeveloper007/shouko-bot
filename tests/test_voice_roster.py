import unittest
from types import SimpleNamespace as S
from unittest.mock import Mock
import discord
import voice_roster as v

class VoiceRoster(unittest.TestCase):
    def setUp(self):
        self.a=S(id=1, display_name='Fazal', bot=False)
        self.b=S(id=2, display_name='@everyone', bot=True)
        self.guild=S(id=10, unavailable=False, me=S(id=99))
        self.members={1:self.a,2:self.b}
        self.guild.get_member=self.members.get
        self.vc=S(id=20,guild=self.guild,type=discord.ChannelType.voice,name='Gaming',voice_states={1:S(),2:S()})
        self.vc.permissions_for=lambda m:S(view_channel=True)
        self.guild.get_channel=lambda cid:self.vc if cid==20 else None
        self.a.voice=S(channel=self.vc)
        self.msg=S(guild=self.guild,author=self.a,content='Kristy VC mein kaun hai?')
        self.client=S(is_ready=lambda:True,intents=S(voice_states=True))
    def get(self,connected=True):return v.snapshot(self.msg,self.client,connected)
    def test_current_members_and_bot_label(self):
        text=self.get();self.assertIn('2 members',text);self.assertIn('[bot]',text);self.assertIn('Fazal',text)
    def test_no_vc_asks_selection(self):
        self.a.voice=None;self.assertIn('Kaunsa VC',self.get())
    def test_explicit_selection_without_join(self):
        self.a.voice=None;self.msg.content='Kristy <#20> mein kaun hai?';self.assertIn('2 members',self.get())
    def test_multiple_mentions(self):
        self.msg.content='Kristy <#20> <#21> mein kaun hai?';self.assertIn('Ek hi',self.get())
    def test_unknown_channel(self):
        self.msg.content='Kristy <#99> mein kaun hai?';self.assertIn('verify nahi',self.get())
    def test_foreign_and_text_channel(self):
        self.vc.guild=S(id=99);self.assertIn('voice/stage',self.get())
        self.vc.guild=self.guild;self.vc.type=discord.ChannelType.text;self.assertIn('text channel nahi',self.get())
    def test_permissions_fail_closed(self):
        self.vc.permissions_for=lambda m:S(view_channel=m.id==99)
        self.assertNotIn('Fazal',self.get());self.assertIn('access',self.get())
        self.vc.permissions_for=lambda m:S(view_channel=m.id!=99)
        self.assertIn('access',self.get())
    def test_not_ready_disconnect_unavailable_intent(self):
        self.assertIn('ready nahi',self.get(False))
        self.client.is_ready=lambda:False;self.assertIn('ready nahi',self.get())
        self.client.is_ready=lambda:True;self.guild.unavailable=True;self.assertIn('ready nahi',self.get())
        self.guild.unavailable=False;self.client.intents.voice_states=False;self.assertIn('ready nahi',self.get())
    def test_unresolved_is_not_empty_or_partial(self):
        del self.members[2];self.assertIn('Adhoori',self.get());self.assertNotIn('Fazal',self.get())
    def test_empty(self):
        self.vc.voice_states={};self.assertIn('khaali',self.get())
    def test_member_and_bot_absent(self):
        self.guild.me=None;self.assertIn('permissions',self.get())
        self.guild.me=S(id=99);del self.members[1];self.assertIn('permissions',self.get())
    def test_channel_disappeared(self):
        self.guild.get_channel=lambda cid:None;self.assertIn('voice/stage',self.get())
    def test_queries(self):
        for text in ('Kristy vc main kaun hai','Kristy who is in voice','Kristy <#20> mein kaun hai','Kristy voice members batao'):
            self.assertTrue(v.is_query(text),text)
        for text in ('Kristy Mayank ko VC join karne bolo','Kristy ek joke sunao','Kristy VC mein log bulao'):
            self.assertFalse(v.is_query(text),text)

class ClarificationTests(unittest.TestCase):
    def setUp(self):
        self.c=v.Clarifications()
        self.m=S(guild=S(id=1),channel=S(id=2),author=S(id=3),content='<#20>',reference=S(message_id=10))
        self.c.remember(self.m,S(id=10))
    def test_exact_reply_or_same_channel_pending(self):
        self.assertTrue(self.c.matches(self.m));self.m.reference=None;self.assertTrue(self.c.matches(self.m))
    def test_other_user_channel_guild_reply_rejected(self):
        for obj in (self.m.author,self.m.channel,self.m.guild):
            old=obj.id;obj.id=999;self.assertFalse(self.c.matches(self.m));obj.id=old
        self.m.reference.message_id=99;self.assertFalse(self.c.matches(self.m))
    def test_expired_consumed_and_unrelated(self):
        self.c.items[self.c.key(self.m)]=(0,10);self.assertFalse(self.c.matches(self.m))
        self.c.remember(self.m,S(id=10));self.m.content='say <#20>';self.assertFalse(self.c.matches(self.m))
        self.m.content='<#20>';self.c.forget(self.m);self.assertFalse(self.c.matches(self.m))
    def test_bare_mention_without_pending_is_not_question(self):
        self.assertFalse(v.is_query('<#20>'));self.c.forget(self.m);self.assertFalse(self.c.matches(self.m))
    def test_multiple_selection_routes_to_snapshot_rejection(self):
        self.m.content='<#20> <#21>';self.assertTrue(self.c.matches(self.m))
    def test_capacity(self):
        self.c.capacity=1;self.m.author.id=4;self.c.remember(self.m,S(id=11));self.assertEqual(len(self.c.items),1)
