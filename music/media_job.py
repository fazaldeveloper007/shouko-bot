"""Credential-free job. Fixed failure codes only, never provider error strings."""
import json
import pathlib
import signal
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from .media import Track

class Quiet:
    def debug(self, *args): pass
    def warning(self, *args): pass
    def error(self, *args): pass

class JobFailure(Exception):
    def __init__(self, code, status=None, stage='MEDIA_JOB'):
        self.code,self.status,self.stage=code,status,stage


def allowed_audio_url(url):
    try:
        u=urllib.parse.urlparse(url)
        return (u.scheme=='https' and not u.username and not u.password and
                u.port in (None,443) and (u.hostname or '').endswith('.googlevideo.com'))
    except (ValueError,TypeError):return False

def reusable_source(info):
    url=info.get('url','')
    if not allowed_audio_url(url) or info.get('ext')!='webm' or info.get('acodec')!='opus':return None
    try:
        expiry=float(urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('expire',['0'])[0])
        track=Track.parse(info,0)
    except Exception:return None
    if expiry<time.time()+45:return None
    headers=info.get('http_headers') or {}
    if not isinstance(headers,dict) or any(not isinstance(k,str) or not isinstance(v,str) or '\r' in k+v or '\n' in k+v for k,v in headers.items()):return None
    return {k:info.get(k) for k in ('id','title','duration','is_live','live_status','url','ext','acodec','http_headers')}

def perform(request):
    from yt_dlp import YoutubeDL
    rows=[]
    opts=dict(quiet=True,no_warnings=True,logger=Quiet(),noplaylist=True,
        js_runtimes={'node':{}},socket_timeout=10,retries=0,extractor_retries=0,
        cachedir=False,skip_download=True,format='bestaudio[ext=webm][acodec=opus]',playlistend=5)
    if request['mode']=='search':opts['extract_flat']='in_playlist'
    start=time.monotonic()
    prepared=request.get('prepared')
    reused=(request['mode']=='download' and isinstance(prepared,dict) and
            prepared.get('id')==request.get('expected_id') and
            0<request.get('prepared_until',0)-time.time()<=31 and reusable_source(prepared) is not None)
    try:
        if reused:info=prepared
        else:
            with YoutubeDL(opts) as y:info=y.extract_info(request['value'],download=False)
    except TimeoutError:raise JobFailure('NETWORK_TIMEOUT',stage='EXTRACT') from None
    except Exception:raise JobFailure('EXTRACT_FAILED',stage='EXTRACT') from None
    rows.append(dict(stage='SOURCE_REUSE' if reused else 'EXTRACT',code='OK',seconds=time.monotonic()-start))
    if request['mode']=='search':
        items=info.get('entries') if info.get('_type') in ('playlist','multi_video') else [info]
        safe=[{k:i.get(k) for k in ('id','title','duration','is_live','live_status')} for i in items if i]
        source=reusable_source(info) if not request['value'].startswith('ytsearch') else None
        return dict(ok=True,items=safe[:5],diagnostics=rows,prepared=source)
    try:track=Track.parse(info,0)
    except Exception:raise JobFailure('BAD_AUDIO',stage='VALIDATE') from None
    if request.get('expected_id') and track.id!=request['expected_id']:raise JobFailure('SOURCE_CHANGED',stage='VALIDATE')
    if track.duration>request['max_seconds']:raise JobFailure('DURATION_LIMIT',stage='VALIDATE')
    url=info.get('url','')
    if not allowed_audio_url(url):raise JobFailure('SOURCE_REJECTED',stage='VALIDATE')
    path=pathlib.Path(request['destination']);total=0;start=time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers=info.get('http_headers') or {}),timeout=15) as response:
            if response.status!=200:raise JobFailure('HTTP_FAILED',response.status,stage='FETCH')
            if not allowed_audio_url(response.geturl()):raise JobFailure('SOURCE_REJECTED',stage='VALIDATE')
            with path.open('xb') as output:
                head=response.read(65536)
                if not head.startswith(b'\x1a\x45\xdf\xa3') or b'A_OPUS' not in head:raise JobFailure('BAD_AUDIO',stage='VALIDATE')
                while head:
                    total+=len(head)
                    if total>request['max_bytes']:raise JobFailure('SIZE_LIMIT',stage='FETCH')
                    output.write(head);head=response.read(65536)
    except urllib.error.HTTPError as exc:raise JobFailure('HTTP_FAILED',exc.code,stage='FETCH') from None
    except (TimeoutError,urllib.error.URLError):raise JobFailure('NETWORK_TIMEOUT',stage='FETCH') from None
    except OSError:raise JobFailure('LOCAL_IO',stage='WRITE') from None
    rows.append(dict(stage='FETCH',code='OK',seconds=time.monotonic()-start,bytes=total))
    return dict(ok=True,id=track.id,bytes=total,diagnostics=rows)


def main():
    request=json.loads(sys.stdin.buffer.read(8192))
    def expired(*args):raise TimeoutError()
    signal.signal(signal.SIGALRM,expired);signal.alarm(90 if request['mode']=='download' else 30)
    try:return perform(request)
    finally:signal.alarm(0)

if __name__=='__main__':
    try:result=main()
    except JobFailure as exc:result=dict(ok=False,code=exc.code,diagnostics=[dict(stage=exc.stage,code=exc.code,status=exc.status)])
    except TimeoutError:result=dict(ok=False,code='NETWORK_TIMEOUT')
    except Exception:result=dict(ok=False,code='WORKER_FAILED')
    print(json.dumps(result));sys.exit(0 if result.get('ok') else 1)
