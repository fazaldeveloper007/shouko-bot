"""Isolated yt-dlp metadata/download jobs. Child receives no bot/API credentials."""
import asyncio
import json
import os
import pathlib
import re
import sys
import time
from .diagnostics import emit, CODES
import urllib.parse
from dataclasses import dataclass

class MediaError(Exception):
    def __init__(self, message, *, code='UNKNOWN'):
        super().__init__(message)
        self.code = code if code in CODES else 'UNKNOWN'

FAILURES = {
    'TEST_LIMIT': 'This controlled test has used its two playback attempts. Finish with /leavevckristy; do not rerun.',
    'DURATION_LIMIT': 'This video exceeds the allowed duration.',
    'EXTRACT_FAILED': 'Audio extraction failed. This item was not played.',
    'HTTP_FAILED': 'The audio download request failed. This item was not played.',
    'NETWORK_TIMEOUT': 'The audio request timed out. This item was not played.',
    'BAD_AUDIO': 'The source did not return supported WebM/Opus audio.',
    'SIZE_LIMIT': 'This audio exceeds the download size limit.',
    'SOURCE_CHANGED': 'Video changed during extraction. This item was not played.',
    'SOURCE_REJECTED': 'The extractor returned an unsupported source.',
    'LOCAL_IO': 'A local file operation failed. This item was not played.',
    'WORKER_FAILED': 'The local audio job stopped before a usable result.',
    'BAD_RESPONSE': 'The local audio job returned an invalid result.',
}

def media_failure(code):
    return MediaError(FAILURES.get(code, 'Audio preparation failed. This item was not played.'), code=code)


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    duration: int
    requester: int

    @property
    def url(self):
        return 'https://www.youtube.com/watch?v=' + self.id

    @classmethod
    def parse(cls, data, requester):
        video_id = str(data.get('id', ''))
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise MediaError('Unsupported video')
        if data.get('is_live') or data.get('live_status') in ('is_live', 'is_upcoming'):
            raise MediaError('Live/upcoming videos are not supported')
        duration = data.get('duration')
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise MediaError('Video duration unavailable')
        return cls(video_id, str(data.get('title') or video_id)[:180], int(duration), requester)


def query_input(query):
    query = query.strip()
    if not query or len(query) > 300:
        raise MediaError('Use a title or one YouTube video link')
    if '://' not in query:
        return 'ytsearch5:' + query
    u = urllib.parse.urlparse(query)
    if u.scheme != 'https' or u.username or u.password or u.port:
        raise MediaError('Use an https YouTube video link')
    host = (u.hostname or '').lower()
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        if u.path == '/watch':
            video_id = urllib.parse.parse_qs(u.query).get('v', [''])[0]
        elif u.path.startswith(('/shorts/', '/live/')):
            video_id = u.path.split('/')[2]
        else:
            raise MediaError('Use one video, not a playlist/channel')
    elif host == 'youtu.be':
        video_id = u.path.strip('/')
    else:
        raise MediaError('Only regular YouTube video links are supported')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise MediaError('Invalid YouTube video link')
    return 'https://www.youtube.com/watch?v=' + video_id

class Media:
    def __init__(self, max_minutes=120, max_bytes=256 * 1024 * 1024):
        self.max_seconds = max_minutes * 60
        self.max_bytes = max_bytes
        self.slots = asyncio.Semaphore(2)
        self.prepared = {}  # short-lived process-local capabilities; never log or persist

    async def job(self, mode, value, destination=None, *, prepared=None, expected_id=None):
        allowed = ('PATH', 'HOME', 'TMPDIR', 'PREFIX', 'LD_LIBRARY_PATH', 'LD_PRELOAD',
                   'SSL_CERT_FILE', 'SSL_CERT_DIR', 'LANG', 'LC_ALL')
        env = {k: os.environ[k] for k in allowed if k in os.environ}
        request = dict(mode=mode, value=value, destination=str(destination) if destination else None,
                       max_seconds=self.max_seconds, max_bytes=self.max_bytes, prepared={k:v for k,v in prepared.items() if k!='_remaining'} if prepared else None, expected_id=expected_id,
                       prepared_until=time.time()+max(0,prepared.get('_remaining',0)) if prepared else 0)
        async with self.slots:
            began = time.monotonic()
            p = await asyncio.create_subprocess_exec(sys.executable, '-m', 'music.media_job',
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, env=env, cwd=pathlib.Path(__file__).resolve().parent.parent)
            try:
                out, _ = await asyncio.wait_for(p.communicate(json.dumps(request).encode()), 100 if destination else 35)
                if len(out) > 65536:
                    raise media_failure('BAD_RESPONSE')
                try:
                    result = json.loads(out)
                except ValueError:
                    raise media_failure('BAD_RESPONSE') from None
                for row in result.get('diagnostics', [])[:8]:
                    if isinstance(row, dict):
                        emit(row.get('stage'), row.get('code'), elapsed=row.get('seconds'), status=row.get('status'), size=row.get('bytes'))
                if p.returncode or not result.get('ok'):
                    raise media_failure(result.get('code', 'WORKER_FAILED'))
                emit('MEDIA_JOB', 'OK', elapsed=time.monotonic()-began)
                return result
            except asyncio.CancelledError:
                emit('MEDIA_JOB', 'CANCELLED', elapsed=time.monotonic()-began)
                raise
            except asyncio.TimeoutError:
                emit('MEDIA_JOB','NETWORK_TIMEOUT',elapsed=time.monotonic()-began)
                raise media_failure('NETWORK_TIMEOUT') from None
            except MediaError as exc:
                emit('MEDIA_JOB',exc.code,elapsed=time.monotonic()-began)
                raise
            finally:
                if p.returncode is None:
                    p.kill()
                await p.wait()

    async def search(self, query, requester):
        value=query_input(query)
        result = await self.job('search', value)
        tracks = []
        for item in result.get('items', [])[:5]:
            try:
                track = Track.parse(item, requester)
                if track.duration <= self.max_seconds:
                    tracks.append(track)
            except MediaError:
                continue
        if not tracks:
            raise MediaError('No supported videos found within the duration limit')
        now=time.monotonic()
        self.prepared={k:v for k,v in self.prepared.items() if v[0]>now}
        source=result.get('prepared')
        if value.startswith('https://') and isinstance(source,dict) and source.get('id')==tracks[0].id and len(json.dumps(source))<4096:
            if len(self.prepared)>=16:self.prepared.pop(next(iter(self.prepared)))
            self.prepared[tracks[0].id]=(now+30,source)
        return tracks

    async def download(self, track, destination):
        cached=self.prepared.pop(track.id,None)
        prepared=dict(cached[1],_remaining=max(0,cached[0]-time.monotonic())) if cached and cached[0]>time.monotonic() else None
        result = await self.job('download', track.url, destination, prepared=prepared, expected_id=track.id)
        if result.get('id') != track.id:
            raise media_failure('SOURCE_CHANGED')
        return pathlib.Path(destination)
