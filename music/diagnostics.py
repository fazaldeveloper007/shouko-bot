"""Fixed-schema local diagnostics. Never accepts exception text, URLs or titles."""
import json
import time

CODES = frozenset(('OK','TEST_LIMIT','DURATION_LIMIT','EXTRACT_FAILED','HTTP_FAILED',
    'NETWORK_TIMEOUT','BAD_AUDIO','SIZE_LIMIT','SOURCE_CHANGED','SOURCE_REJECTED',
    'LOCAL_IO','WORKER_FAILED','BAD_RESPONSE','CANCELLED','PANEL_FAILED','UNKNOWN'))
STAGES = frozenset(('SEARCH','EXTRACT','FETCH','VALIDATE','WRITE','MEDIA_JOB','PLAY_START','PANEL','VOICE_CONNECT','REQUEST','QUEUE_WAIT','VOICE_PLAY','SOURCE_REUSE'))

def emit(stage, code, *, elapsed=None, status=None, size=None):
    row = {'stage': stage if stage in STAGES else 'MEDIA_JOB',
           'code': code if code in CODES else 'UNKNOWN'}
    if isinstance(elapsed, (int,float)) and elapsed >= 0: row['ms'] = round(elapsed * 1000)
    if type(status) is int and 100 <= status <= 599: row['status'] = status
    if type(size) is int and size >= 0: row['bytes'] = size
    print('MUSIC_DIAG ' + json.dumps(row, sort_keys=True), flush=True)
