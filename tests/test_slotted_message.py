"""discord.Message has __slots__: the reminder followup gate must never set attributes on it."""
import unittest
from unittest.mock import patch
import schedule_commands as sc

class SlottedMessage:
    __slots__ = ('id', 'author', 'reference', 'content')
    def __init__(self, mid=1): self.id = mid; self.author = None; self.reference = None; self.content = 'x'

class SlottedFollowup(unittest.TestCase):
    def make(self):
        obj = sc.Commands.__new__(sc.Commands)
        obj.helper_addressed = {}
        return obj
    def test_followup_with_helper_item_does_not_write_to_message(self):
        c = self.make(); m = SlottedMessage(5)
        with patch.object(sc.Commands, 'pending_item', return_value=None), patch.object(sc.Commands, 'helper_item', return_value=('k', ('a', 'b'))):
            self.assertTrue(c.followup(m, 'ok', addressed=True))
        with self.assertRaises(AttributeError): m.anything = 1   # proves the stand-in really rejects new attributes
        self.assertTrue(c.was_helper_addressed(m))
        self.assertFalse(c.was_helper_addressed(SlottedMessage(6)))
    def test_not_addressed_is_remembered_as_false(self):
        c = self.make(); m = SlottedMessage(7)
        with patch.object(sc.Commands, 'pending_item', return_value=None), patch.object(sc.Commands, 'helper_item', return_value=('k', ('a', 'b'))):
            c.followup(m, 'ok', addressed=False)
        self.assertFalse(c.was_helper_addressed(m))
    def test_memory_is_bounded(self):
        c = self.make()
        for i in range(800): c.mark_helper_addressed(SlottedMessage(i), True)
        self.assertLessEqual(len(c.helper_addressed), 500)
        self.assertTrue(c.was_helper_addressed(SlottedMessage(799)))
