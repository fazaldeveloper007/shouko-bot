"""Silent deletion of Kristy's own message when a member replies to it with a
plain delete request. Never deletes anyone else's message, never takes an ID
or a count from text, and never acts on quoted/embedded text."""
import logging
import re

log = logging.getLogger('kristy.delete')
_DEMO = r'(?:ye|yeh|is|iss|isse|ise|isko|usko|this|that|wo|woh|vo|us|usse|use)'
_NOUN = r'(?:message|msg|reply|text)'
_VERB = r'(?:delete|del|remove|hata|hatao|hatado|hta|mita|mitao|mitado)'
_TAIL = r'(?:kar|karo|kardo|do|dena|de|dijiye|plz|please|ok|bhai|na)'
DELETE = re.compile(
    rf'(?:(?:ok+|okay|please|plz|bhai|kristy)[\s,]+)*(?:{_DEMO}\s+)*(?:{_NOUN}\s+)?(?:ko\s+)?{_VERB}'
    rf'(?:\s+{_TAIL})*(?:\s+{_DEMO})?(?:\s+{_NOUN})?(?:\s+{_TAIL})*[\s.!]*', re.I)


def is_request(text):
    return bool(DELETE.fullmatch((text or '').strip()))


class Deleter:
    def __init__(self, owner_id='', bot_id=lambda: None):
        self.owner_id, self.bot_id = owner_id, bot_id

    def allowed(self, message, target, bot_id):
        """Requester may delete Kristy's message if they are the bot owner,
        the member Kristy was replying to, or can manage messages there."""
        author = message.author
        if self.owner_id and str(author.id) == self.owner_id:
            return True
        ref = getattr(getattr(target, 'reference', None), 'resolved', None)
        if ref is not None and getattr(getattr(ref, 'author', None), 'id', None) == author.id:
            return True
        try:
            return bool(message.channel.permissions_for(author).manage_messages)
        except Exception:
            return False

    async def handle(self, message, text, guard):
        ref = getattr(message, 'reference', None)
        if ref is None or not is_request(text):
            return False
        if guard(message, text, question=True) is None:
            return False
        bot_id = self.bot_id()
        if bot_id is None or message.guild is None or getattr(message, 'message_snapshots', None):
            return False
        target = getattr(ref, 'resolved', None)
        if target is None or not hasattr(target, 'author'):
            try:
                target = await message.channel.fetch_message(ref.message_id)
            except Exception:
                return False
        # Only Kristy's own message, in this very channel.
        if (getattr(target.author, 'id', None) != bot_id
                or getattr(getattr(target, 'channel', None), 'id', None) != message.channel.id
                or getattr(target, 'id', None) != ref.message_id):
            return False
        if not self.allowed(message, target, bot_id):
            return False
        try:
            await target.delete()
        except Exception as exc:
            log.warning('Own-message delete failed: %s', type(exc).__name__)
        return True
