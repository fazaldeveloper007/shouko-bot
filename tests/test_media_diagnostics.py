import asyncio,contextlib,io,json,pathlib,tempfile,types,unittest
from unittest.mock import AsyncMock,patch
from music.media import Media,MediaError,Track,media_failure
from music.diagnostics import emit
from music.media_job import perform,JobFailure
class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
 async def test_child_failure_structured_no_exception_body(self):
  p=types.SimpleNamespace(returncode=1,communicate=AsyncMock(return_value=(json.dumps({'ok':False,'code':'HTTP_FAILED','diagnostics':[{'stage':'FETCH','code':'HTTP_FAILED','status':403,'seconds':.12,'url':'SECRET'}]}).encode(),b'')),wait=AsyncMock())
  with patch('asyncio.create_subprocess_exec',new=AsyncMock(return_value=p)),contextlib.redirect_stdout(io.StringIO()) as out:
   with self.assertRaises(MediaError) as e:await Media().job('download','unused','/tmp/unused')
  self.assertEqual(e.exception.code,'HTTP_FAILED');self.assertNotIn('SECRET',out.getvalue());self.assertIn('403',out.getvalue())
 async def test_safe_schema_rejects_secrets(self):
  with contextlib.redirect_stdout(io.StringIO()) as out:emit('https://SECRET','SECRET',elapsed=.1,status=403)
  self.assertNotIn('SECRET',out.getvalue());self.assertIn('UNKNOWN',out.getvalue())
 async def test_local_download_validation_and_disk_error(self):
  class Y:
   def __init__(self,*a,**k):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def extract_info(self,*a,**k):return dict(id='jNQXAC9IVRw',title='test',duration=19,url='https://media.googlevideo.com/audio')
  class R:
   status=200
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def geturl(self):return 'https://media.googlevideo.com/audio'
   def read(self,n):return b'not-webm'
  request=dict(mode='download',value='unused',destination='/tmp/unused',max_seconds=60,max_bytes=100)
  with tempfile.TemporaryDirectory() as d,patch('yt_dlp.YoutubeDL',Y),patch('urllib.request.urlopen',return_value=R()):
   request['destination']=str(pathlib.Path(d)/'a.webm')
   with self.assertRaises(JobFailure) as e:perform(request)
   self.assertEqual(e.exception.code,'BAD_AUDIO')
