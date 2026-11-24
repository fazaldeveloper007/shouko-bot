import datetime as dt
from unittest.mock import AsyncMock
from test_reminder_intent import Base

class RoleSafety(Base):
    async def test_role_is_not_silently_downgraded_to_requester(self):
        self.ai(dict(action='remind',when='every monday 8 PM',task='save resin for weekly bosses',tag_names=[]))
        m=self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m,'remind <@&123456789012345678> every Monday to save resin for weekly bosses at 8 PM')
        self.assertEqual(self.store.load(),[])
        self.assertIn('save nahi hua',m.reply.call_args.args[0])
        self.assertEqual(self.calls,[])
    async def test_role_in_completed_followup_cannot_save_author_only(self):
        self.ai(dict(action='need_info',question='Kab?'),dict(action='remind',when='10 minute baad',task='save resin',tag_names=[]))
        m=self.msg(dt.datetime.now(dt.timezone.utc))
        await self.run_msg(m,'remind me to save resin')
        follow=self.msg(m.created_at)
        await self.run_msg(follow,'10 minute baad tag <@&123456789012345678>')
        self.assertEqual(self.store.load(),[])
        self.assertIn('save nahi hua',follow.reply.call_args.args[0])
