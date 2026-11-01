import tempfile,pathlib,types,unittest
from unittest.mock import AsyncMock
from music.commands import register_commands
class CommandTests(unittest.IsolatedAsyncioTestCase):
 async def test_global_additive_owned_update_and_collision(self):
  remote=[{'id':'1','name':'other'}]
  async def read(*a):return list(remote)
  async def add(*a,payload):
   r={'id':str(len(remote)+1),'name':payload['name']};remote.append(r);return r
  async def edit(app,cid,payload):return next(v for v in remote if int(v['id'])==cid)
  h=types.SimpleNamespace(get_global_commands=AsyncMock(side_effect=read),upsert_global_command=AsyncMock(side_effect=add),edit_global_command=AsyncMock(side_effect=edit))
  c=types.SimpleNamespace(application_id=123,http=h);t=types.SimpleNamespace(get_commands=lambda:[types.SimpleNamespace(name='play',to_dict=lambda t:{'name':'play'})])
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'owned.json'
   await register_commands(c,t,ledger_path=p);self.assertEqual(len(remote),2)
   await register_commands(c,t,ledger_path=p);h.edit_global_command.assert_awaited_once();self.assertEqual(remote[0]['name'],'other')
   p.unlink()
   with self.assertRaises(RuntimeError):await register_commands(c,t,ledger_path=p)
   self.assertEqual(h.upsert_global_command.await_count,1)
