"""One bounded request budget shared across provider and compatibility paths."""
import asyncio
import contextvars
import json
import functools
import logging
import time
from contextlib import asynccontextmanager
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import aiohttp

log = logging.getLogger('kristy.retry')
_current = contextvars.ContextVar('request_budget', default=None)

# Operation context for API-call log lines: each async task labels what its
# calls are FOR ("replying to X in Y", "welcome line for Z"), so bare
# "Gemini call attempt" lines stay attributable when several background jobs
# run at once. ContextVar = per-task, no cross-contamination between
# concurrent operations. Never carries message text, prompts or keys - only
# the operation kind plus bounded sanitized names.
_op = contextvars.ContextVar('retrying_op', default='')

_NAME_CTRL = __import__('re').compile(r'[\x00-\x1f\x7f]')

def clean_name(value, limit=32):
    """Bounded, control-char-free name safe for a log line."""
    text = _NAME_CTRL.sub(' ', str(value or '')).strip()
    return text[:limit] or '?'

def set_op(label):
    """Label this task's API calls; returns the token for reset_op."""
    return _op.set(str(label or '')[:120])

def reset_op(token):
    _op.reset(token)

def op_suffix():
    op = _op.get()
    return f' op="{op}"' if op else ''

class Budget:
    def __init__(self):
        self.used = 0
        self.deadline = time.monotonic() + 60
    def seconds(self):
        return max(0, self.deadline - time.monotonic())

class RequestError(RuntimeError):
    def __init__(self, label, status=None, data=None, reason='request'):
        super().__init__(f'{label} request failed (HTTP {status})' if status else f'{label} request failed')
        self.status, self.data, self.reason = status, data, reason

@asynccontextmanager
async def request_scope():
    token = _current.set(Budget())
    try:
        yield
    finally:
        _current.reset(token)

def operation(func):
    """Distinct successful pipeline steps are not retry attempts.

    Each generation (including provider failover) or search operation gets
    three calls; it inherits the enclosing response's remaining deadline.
    """
    @functools.wraps(func)
    async def wrapped(*args, **kwargs):
        parent = _current.get()
        budget = Budget()
        if parent is not None:
            budget.deadline = parent.deadline
        token = _current.set(budget)
        try:
            return await func(*args, **kwargs)
        finally:
            _current.reset(token)
    return wrapped

FALLBACK_MIN_SECONDS = 15

@asynccontextmanager
async def fallback_budget():
    """Own three-call budget for a provider failover.

    A failed primary provider may have used all three calls of the operation's
    budget; the fallback provider must still get a real try, or the fallback
    can never run after a transient primary outage (HTTP 503). The deadline is
    the operation's, extended to leave the fallback at least
    FALLBACK_MIN_SECONDS. Calls per provider stay bounded at three.
    """
    parent = _current.get()
    budget = Budget()
    if parent is not None:
        budget.deadline = max(parent.deadline, time.monotonic() + FALLBACK_MIN_SECONDS)
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)

def retry_delay(value, fallback):
    if not value:
        return fallback
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None  # unknown mandate: do not guess an earlier retry
    return max(0, seconds) if seconds <= 8 else None

def generation_validator(provider, usable=lambda text: bool(text.strip())):
    """Validate HTTP200 generation output before declaring the call successful.

    Explicit policy blocks are terminal. Missing/malformed/empty output is
    retryable within the same HTTP budget, never an extra outer retry loop.
    """
    def validate(data):
        try:
            if provider == 'Gemini':
                feedback = data.get('promptFeedback') or {}
                if feedback.get('blockReason'):
                    return 'blocked'
                candidate = data['candidates'][0]
                reason = candidate.get('finishReason')
                if reason in ('MALFORMED_RESPONSE', 'MALFORMED_FUNCTION_CALL'):
                    return 'malformed_output'
                if reason and reason not in ('STOP', 'MAX_TOKENS', 'FINISH_REASON_UNSPECIFIED'):
                    return 'blocked'
                parts = candidate['content']['parts']
                text = ''.join(p.get('text', '') for p in parts if isinstance(p, dict) and not p.get('thought'))
            else:
                choice = data['choices'][0]
                if choice.get('finish_reason') in ('content_filter', 'safety') or (choice.get('message') or {}).get('refusal'):
                    return 'blocked'
                text = choice['message']['content']
            return None if isinstance(text, str) and usable(text) else 'empty_output'
        except (KeyError, IndexError, TypeError, AttributeError, ValueError):
            return 'malformed_output'
    return validate


async def post_json(session, url, *, label, headers, payload, timeout=20, validate=None):
    """Only idempotent generation/search requests, never message sends.

    Three HTTP attempts including initial, across all providers and config
    corrections in the current scope. Whole scope has a 60s deadline.
    """
    budget = _current.get()
    if budget is None:
        async with request_scope():
            return await post_json(session, url, label=label, headers=headers,
                                   payload=payload, timeout=timeout, validate=validate)
    status, data, reason = None, None, 'deadline'
    while budget.used < 3 and budget.seconds() > .1:
        budget.used += 1
        status, data, wait = None, None, min(2, budget.used)
        reason = 'transport'
        log.info('%s call attempt=%d/3%s', label, budget.used, op_suffix())
        try:
            async with session.post(url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=min(timeout, budget.seconds()), ceil_threshold=1000)) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    data = None
                if status == 200 and isinstance(data, dict):
                    reason = validate(data) if validate else None
                    if reason == 'blocked':
                        log.warning('%s stopped attempt=%d/3 status=200 reason=blocked%s', label, budget.used, op_suffix())
                        raise RequestError(label, status, reason='blocked')
                    if reason is None:
                        log.info('%s complete attempt=%d/3 status=200%s', label, budget.used, op_suffix())
                        return data
                elif status == 200:
                    reason = 'malformed_json'
                else:
                    reason = 'http'
                log.warning('%s failed attempt=%d/3 status=%s reason=%s%s', label, budget.used, status, reason, op_suffix())
                if status == 400 and label == 'Gemini':
                    # Optional-model-field fix consumes the SAME budget.
                    config = payload.get('generationConfig') or {}
                    error = str(data).lower().replace('_', '')
                    field = next((k for k in ('thinkingConfig', 'responseMimeType')
                                  if k in config and k.lower() in error), None)
                    if field:
                        config.pop(field)
                        continue
                if status == 429:
                    # Quota failures do not heal in seconds.
                    body = str(data).lower()
                    if any(x in body for x in ('quota', 'billing', 'daily', 'perday', 'per_day', 'insufficient')):
                        raise RequestError(label, status, reason='quota')
                    wait = retry_delay(resp.headers.get('Retry-After'), wait)
                    if wait is None:
                        raise RequestError(label, status, reason='retry_after')
                elif status != 200 and status not in (408, 500, 502, 503, 504):
                    raise RequestError(label, status, reason='permanent_http')
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # Error class separates DNS/connect/TLS/timeout failures in logs.
            # Keys travel in headers, which aiohttp exceptions never include.
            detail = type(exc).__name__
            cause = getattr(exc, 'os_error', None)
            if cause is not None:
                detail += f'({getattr(cause, "errno", None) or type(cause).__name__})'
            log.warning('%s failed attempt=%d/3 status=%s reason=transport error=%s%s', label, budget.used, status, detail, op_suffix())
        if budget.used >= 3 or wait >= budget.seconds():
            break
        log.warning('%s transient failure; attempt %d/3%s', label, budget.used, op_suffix())
        await asyncio.sleep(wait)
    log.warning('%s exhausted calls=%d/3 status=%s reason=budget_or_deadline%s', label, budget.used, status, op_suffix())
    raise RequestError(label, status, reason=reason)
