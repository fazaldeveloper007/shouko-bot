"""Fixed-schema local diagnostics. Never accepts exception text, URLs or titles."""
import json
import time

CODES = frozenset(('OK','TEST_LIMIT','DURATION_LIMIT','EXTRACT_FAILED','HTTP_FAILED',
    'NETWORK_TIMEOUT','NETWORK_FAILED','BAD_AUDIO','SIZE_LIMIT','SOURCE_CHANGED','SOURCE_REJECTED',
    'LOCAL_IO','WORKER_FAILED','BAD_RESPONSE','CANCELLED','PANEL_FAILED','ACK_TIMEOUT','UNKNOWN'))
STAGES = frozenset(('SEARCH','EXTRACT','FETCH','VALIDATE','WRITE','MEDIA_JOB','PLAY_START','PANEL','VOICE_CONNECT','REQUEST','QUEUE_WAIT','VOICE_PLAY','SOURCE_REUSE','SKIP'))

def emit(stage, code, *, elapsed=None, status=None, size=None):
    row = {'stage': stage if stage in STAGES else 'MEDIA_JOB',
           'code': code if code in CODES else 'UNKNOWN'}
    if isinstance(elapsed, (int,float)) and elapsed >= 0: row['ms'] = round(elapsed * 1000)
    if type(status) is int and 100 <= status <= 599: row['status'] = status
    if type(size) is int and size >= 0: row['bytes'] = size
    print('MUSIC_DIAG ' + json.dumps(row, sort_keys=True), flush=True)

ENGINE_CODES = frozenset(('INVALID_COMMAND','MEDIA_READ_FAILED','PLAYER_FAILED','VOICE_ERROR','VOICE_DISCONNECTED',
    'VOICE_STATE_CHANGED','GATEWAY_ACK_TIMEOUT','GATEWAY_SEND_FAILED','DAVE_TRANSITION_TIMEOUT','DAVE_READINESS_LOST','DAVE_WAIT_FAILED',
    'VOICE_REMOVED','VOICE_MOVED','VOICE_SESSION_CHANGED','VOICE_WORKER_EXITED','VOICE_IPC_FAILED','IPC_TOO_LARGE'))

def engine_code(code):
    """Worker-reported failure code, only if it is one of the fixed known names."""
    return code if isinstance(code, str) and code in ENGINE_CODES else 'OTHER'

def engine_diag(code):
    """Why the voice engine ended the session. Fixed names only, never text."""
    name = code[7:] if isinstance(code, str) and code.startswith('WORKER_') else code
    print('MUSIC_DIAG ' + json.dumps({'stage': 'ENGINE', 'engine_code': engine_code(name),
          'source': 'worker' if isinstance(code, str) and code.startswith('WORKER_') else 'python'}, sort_keys=True), flush=True)

def engine_label(code):
    name = code[7:] if isinstance(code, str) and code.startswith('WORKER_') else code
    return engine_code(name)
