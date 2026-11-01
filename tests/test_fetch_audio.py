import io,pathlib,tempfile,unittest
from music.media_job import fetch_audio,JobFailure
AUDIO=b'\x1a\x45\xdf\xa3'+b'\x00'*30+b'A_OPUS'+b'x'*(2*1024*1024)
class Response(io.BytesIO):
 def __init__(self,data,status=206,headers=None):super().__init__(data);self.status=status;self.headers=headers or {}
 def geturl(self):return 'https://a.googlevideo.com/audio'
class FetchTests(unittest.TestCase):
 def test_range_chunks_exact_file(self):
  calls=[]
  def open_(request,timeout):
   a,b=map(int,request.get_header('Range').split('=')[1].split('-'));b=min(b,len(AUDIO)-1);calls.append((a,b))
   return Response(AUDIO[a:b+1],headers={'Content-Range':f'bytes {a}-{b}/{len(AUDIO)}'})
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'a';self.assertEqual(fetch_audio('https://a.googlevideo.com/a',{},p,len(AUDIO)+10,opener=open_),len(AUDIO));self.assertEqual(p.read_bytes(),AUDIO)
  self.assertEqual(len(calls),3)
 def test_bad_range_and_truncation_rejected(self):
  for h,data in (({'Content-Range':'bytes 1-5/10'},b'12345'),({'Content-Range':'bytes 0-9/10'},b'123')):
   with tempfile.TemporaryDirectory() as d:
    with self.assertRaises(JobFailure) as e:fetch_audio('https://a.googlevideo.com/a',{},pathlib.Path(d)/'a',100,opener=lambda *a,**k:Response(data,headers=h))
    self.assertIn(e.exception.code,('BAD_RESPONSE','BAD_AUDIO'))
 def test_progress_bytes_on_stall(self):
  class Stall(Response):
   def read1(self,n):
    if self.tell():raise TimeoutError('private')
    return super().read1(100)
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(JobFailure) as e:fetch_audio('https://a.googlevideo.com/a',{},pathlib.Path(d)/'a',len(AUDIO)+10,opener=lambda *a,**k:Stall(AUDIO,status=200,headers={'Content-Length':str(len(AUDIO))}))
   self.assertEqual(e.exception.code,'NETWORK_TIMEOUT');self.assertEqual(e.exception.size,100)
 def test_server_ignores_first_range_valid_full_download(self):
  with tempfile.TemporaryDirectory() as d:
   self.assertEqual(fetch_audio('https://a.googlevideo.com/a',{},pathlib.Path(d)/'a',len(AUDIO)+10,opener=lambda *a,**k:Response(AUDIO,200,{'Content-Length':str(len(AUDIO))})),len(AUDIO))
 def test_real_http_range_response_and_read1(self):
  import http.server,threading
  from unittest.mock import patch
  class Handler(http.server.BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_GET(self):
    a,b=map(int,self.headers['Range'].split('=')[1].split('-'));b=min(b,len(AUDIO)-1)
    self.send_response(206);self.send_header('Content-Range',f'bytes {a}-{b}/{len(AUDIO)}');self.send_header('Content-Length',str(b-a+1));self.end_headers();self.wfile.write(AUDIO[a:b+1])
  server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory() as d,patch('music.media_job.allowed_audio_url',return_value=True):
    p=pathlib.Path(d)/'a';self.assertEqual(fetch_audio(f'http://127.0.0.1:{server.server_port}/audio',{},p,len(AUDIO)+10),len(AUDIO));self.assertEqual(p.read_bytes(),AUDIO)
  finally:server.shutdown();server.server_close();thread.join()
