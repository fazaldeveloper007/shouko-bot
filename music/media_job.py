"""Credential-free job. Fixed failure codes only, never provider error strings."""
import json
import pathlib
import signal
import socket
import re
import ssl
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
    return {k:info.get(k) for k in ('id','title','duration','abr','is_live','live_status','url','ext','acodec','http_headers')}

def _perform(request, rows):
    from yt_dlp import YoutubeDL
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
        elif request['mode']=='related':
            opts['extract_flat']='in_playlist';opts['playlistend']=15
            # Primary: YouTube's own radio mix for the seed video. The mix needs
            # playlist processing: with noplaylist=True yt-dlp resolves the URL to
            # the bare video and returns no entries at all, which also wrongly
            # suppressed the fallback (yt-dlp YoutubeTabIE._yes_playlist).
            # Fallback: artist search built from the seed title's "Artist - Title"
            # shape; also used when the mix extracts but yields no entries.
            vid=request['value']
            sources=['https://www.youtube.com/watch?v=%s&list=RD%s' % (vid,vid)]
            artist=re.split(r'\s[-\u2013\u2014|]\s',request.get('related_title') or '')[0].strip()
            if len(artist)>=2:sources.append('ytsearch15:%s' % artist)
            info=last=None
            for source in sources:
                attempt=dict(opts)
                if 'list=RD' in source:attempt['noplaylist']=False
                try:
                    with YoutubeDL(attempt) as y:info=y.extract_info(source,download=False)
                    if info.get('entries'):break
                except Exception as exc:last=exc;info=None
            if info is None:raise last
        else:
            with YoutubeDL(opts) as y:info=y.extract_info(request['value'],download=False)
    except TimeoutError:raise JobFailure('NETWORK_TIMEOUT',stage='EXTRACT') from None
    except Exception:raise JobFailure('EXTRACT_FAILED',stage='EXTRACT') from None
    rows.append(dict(stage='SOURCE_REUSE' if reused else 'EXTRACT',code='OK',seconds=time.monotonic()-start))
    if request['mode']=='related':
        items=info.get('entries') or []
        safe=[{k:i.get(k) for k in ('id','title','duration','is_live','live_status')} for i in items if i]
        return dict(ok=True,items=safe,diagnostics=rows)
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
    path=pathlib.Path(request['destination']);start=time.monotonic()
    try:total=fetch_audio(url,info.get('http_headers') or {},path,request['max_bytes'])
    except JobFailure as exc:
        exc.seconds=time.monotonic()-start
        raise
    rows.append(dict(stage='FETCH',code='OK',seconds=time.monotonic()-start,bytes=total))
    return dict(ok=True,id=track.id,bytes=total,diagnostics=rows)


def fetch_audio(url, headers, path, maximum, *, opener=None):
    # Range requests avoid an unbounded whole-file request. Never splice mismatched ranges.
    opener=opener or urllib.request.urlopen
    total=0;expected=None;chunk=1024*1024;prefix=bytearray();validated=False
    try:
        with path.open('xb') as output:
            while True:
                end=min(total+chunk-1,maximum)
                h={k:v for k,v in headers.items() if k.lower() not in ('range','accept-encoding')}
                h.update(Range=f'bytes={total}-{end}',**{'Accept-Encoding':'identity'})
                with opener(urllib.request.Request(url,headers=h),timeout=15) as response:
                    if not allowed_audio_url(response.geturl()):raise JobFailure('SOURCE_REJECTED',stage='VALIDATE')
                    ranged=response.status==206
                    if ranged:
                        m=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                        if not m:raise JobFailure('BAD_RESPONSE',stage='FETCH')
                        first,last,length=map(int,m.groups())
                        if first!=total or last<first or last>end or last>=length or (expected is not None and expected!=length):raise JobFailure('BAD_RESPONSE',stage='FETCH')
                        expected=length;promised=last-first+1
                    elif response.status==200 and total==0:
                        length=response.headers.get('Content-Length')
                        if length and not length.isdigit():raise JobFailure('BAD_RESPONSE',stage='FETCH')
                        expected=int(length) if length else None;promised=expected
                    else:raise JobFailure('HTTP_FAILED',response.status,stage='FETCH')
                    if expected is not None and expected>maximum:raise JobFailure('SIZE_LIMIT',stage='FETCH')
                    received=0
                    while True:
                        reader=getattr(response,'read1',response.read)
                        data=reader(16384)
                        if not data:break
                        received+=len(data);total+=len(data)
                        if total>maximum:raise JobFailure('SIZE_LIMIT',stage='FETCH')
                        if promised is not None and received>promised:raise JobFailure('BAD_RESPONSE',stage='FETCH')
                        if not validated:
                            prefix.extend(data[:max(0,65536-len(prefix))])
                            if len(prefix)>=4 and not prefix.startswith(b'\x1a\x45\xdf\xa3'):raise JobFailure('BAD_AUDIO',stage='VALIDATE')
                            validated=b'A_OPUS' in prefix
                            if len(prefix)>=65536 and not validated:raise JobFailure('BAD_AUDIO',stage='VALIDATE')
                        output.write(data)
                    if promised is not None and received!=promised:raise JobFailure('BAD_RESPONSE',stage='FETCH')
                    if not ranged or total==expected:break
                    if received==0:raise JobFailure('BAD_RESPONSE',stage='FETCH')
            if not validated:raise JobFailure('BAD_AUDIO',stage='VALIDATE')
        return total
    except urllib.error.HTTPError as exc:failure=JobFailure('HTTP_FAILED',exc.code,stage='FETCH')
    except TimeoutError:failure=JobFailure('NETWORK_TIMEOUT',stage='FETCH')
    except urllib.error.URLError as exc:
        failure=JobFailure('NETWORK_TIMEOUT' if isinstance(exc.reason,(TimeoutError,socket.timeout)) else 'NETWORK_FAILED',stage='FETCH')
    except ssl.SSLError:failure=JobFailure('NETWORK_FAILED',stage='FETCH')
    except JobFailure as exc:failure=exc
    except OSError:failure=JobFailure('LOCAL_IO',stage='WRITE')
    failure.size=total
    raise failure from None

def perform(request):
    rows=[]
    began=time.monotonic()
    try:return _perform(request,rows)
    except JobFailure as exc:
        exc.diagnostics=rows+[dict(stage=exc.stage,code=exc.code,status=exc.status,seconds=getattr(exc,'seconds',None),bytes=getattr(exc,'size',None))]
        raise
    except TimeoutError:
        exc=JobFailure('NETWORK_TIMEOUT',stage='FETCH' if rows else 'EXTRACT')
        exc.diagnostics=rows+[dict(stage=exc.stage,code=exc.code,seconds=time.monotonic()-began)]
        raise exc from None

def main():
    request=json.loads(sys.stdin.buffer.read(8192))
    def expired(*args):raise TimeoutError()
    signal.signal(signal.SIGALRM,expired);signal.alarm(90 if request['mode']=='download' else 30)
    try:return perform(request)
    finally:signal.alarm(0)

if __name__=='__main__':
    try:result=main()
    except JobFailure as exc:result=dict(ok=False,code=exc.code,diagnostics=getattr(exc,'diagnostics',[dict(stage=exc.stage,code=exc.code,status=exc.status,seconds=getattr(exc,'seconds',None),bytes=getattr(exc,'size',None))]))
    except TimeoutError:result=dict(ok=False,code='NETWORK_TIMEOUT')
    except Exception:result=dict(ok=False,code='WORKER_FAILED')
    print(json.dumps(result));sys.exit(0 if result.get('ok') else 1)
