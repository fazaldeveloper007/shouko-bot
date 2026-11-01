import asyncio,pathlib,time,tempfile,types,unittest
from unittest.mock import AsyncMock,patch
from music.media import Media,Track,MediaError
from music.media_job import perform,JobFailure,reusable_source

def source(**kw):
 d=dict(id='jNQXAC9IVRw',title='test',duration=19,url='https://media.googlevideo.com/audio?expire='+str(int(time.time()+3600)),ext='webm',acodec='opus',http_headers={});d.update(kw);return d
class ReuseTests(unittest.IsolatedAsyncioTestCase):
 async def test_exact_link_single_use_and_expiry(self):
  m=Media();m.job=AsyncMock(return_value={'items':[source()],'prepared':source(),'id':'jNQXAC9IVRw'})
  t=(await m.search('https://www.youtube.com/watch?v=jNQXAC9IVRw',1))[0]
  await m.download(t,'unused');self.assertIsNotNone(m.job.call_args.kwargs['prepared'])
  await m.download(t,'unused');self.assertIsNone(m.job.call_args.kwargs['prepared'])
  await m.search(t.url,1);m.prepared[t.id]=(time.monotonic()-1,source())
  await m.download(t,'unused');self.assertIsNone(m.job.call_args.kwargs['prepared'])
 async def test_keyword_not_cached(self):
  m=Media();m.job=AsyncMock(return_value={'items':[source()],'prepared':source()})
  await m.search('hello',1);self.assertFalse(m.prepared)
 def test_bad_capabilities_rejected(self):
  for kw in ({'url':'https://evil.invalid/a'},{'url':'https://a.googlevideo.com@evil.invalid/a'},{'url':'https://a.googlevideo.com:444/a'},{'acodec':'aac'},{'duration':0},{'http_headers':{'Cookie':'x\r\ny'}},{'url':'https://a.googlevideo.com/a?expire=1'}):
   self.assertIsNone(reusable_source(source(**kw)))
 def test_valid_reuse_skips_extractor_and_validates_webm(self):
  class Y:
   def __init__(self,*a,**k):raise AssertionError('must not extract')
  class R:
   status=200;headers={}
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def geturl(self):return 'https://a.googlevideo.com/a'
   def read(self,n):return b'bad-audio'
  with tempfile.TemporaryDirectory() as d,patch('yt_dlp.YoutubeDL',Y),patch('urllib.request.urlopen',return_value=R()):
   with self.assertRaises(JobFailure) as e:perform(dict(mode='download',value='unused',prepared=source(),prepared_until=time.time()+25,expected_id='jNQXAC9IVRw',max_seconds=60,max_bytes=100,destination=str(pathlib.Path(d)/'x')))
   self.assertEqual(e.exception.code,'BAD_AUDIO')
 def test_stale_or_mismatched_capability_extracts_fresh(self):
  class Y:
   def __init__(self,*a,**k):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def extract_info(self,*a,**k):raise RuntimeError('safe mock')
  for expiry,expected in ((time.time()-1,'jNQXAC9IVRw'),(time.time()+25,'aaaaaaaaaaa')):
   with patch('yt_dlp.YoutubeDL',Y):
    with self.assertRaises(JobFailure) as e:perform(dict(mode='download',value='unused',prepared=source(),prepared_until=expiry,expected_id=expected))
    self.assertEqual(e.exception.code,'EXTRACT_FAILED')
 def test_fetch_connection_vs_timeout_preserves_extract_timing(self):
  import urllib.error
  class Y:
   def __init__(self,*a,**k):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def extract_info(self,*a,**k):return source()
  for reason,code in ((TimeoutError('private error text'),'NETWORK_TIMEOUT'),(OSError('private DNS text'),'NETWORK_FAILED')):
   with tempfile.TemporaryDirectory() as d,patch('yt_dlp.YoutubeDL',Y),patch('urllib.request.urlopen',side_effect=urllib.error.URLError(reason)):
    with self.assertRaises(JobFailure) as e:perform(dict(mode='download',value='unused',max_seconds=60,max_bytes=100,destination=str(pathlib.Path(d)/'audio')))
    self.assertEqual(e.exception.code,code)
    self.assertEqual(e.exception.diagnostics[0]['stage'],'EXTRACT')
    self.assertEqual(e.exception.diagnostics[-1]['stage'],'FETCH')
    self.assertNotIn('private',str(e.exception.diagnostics))
