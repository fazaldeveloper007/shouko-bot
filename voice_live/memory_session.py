"""Inactive adapters for natural Live. No live entrypoint imports this module.

One explicit end-of-session call is supplied by the future runtime; this file
has no network sender, credentials, file reads, saves or automatic retries.
"""
import asyncio
import copy
from voice_live.memory import (MemoryErrorCode, TranscriptBuffer, selected_context,
    extraction_request, parse_candidates, SHORT_PERSONA, MAX_TURNS)
from voice_live.memory_policy import safe_candidates
from voice_live.duplex_protocol import DuplexTurn


class SessionMemory:
    def __init__(self, *, selection, requester, guild, generation, facts):
        self.owner = requester
        self.context = selected_context(selection, requester=requester, guild=guild,
                                        generation=generation, facts=facts)
        self.buffer = TranscriptBuffer(requester)
        self.segments = 0
        self.skipped = 0
        self.state = 'COLLECTING'
        self.status = 'MEMORY_PENDING'

    def setup(self, config):
        config = copy.deepcopy(config)
        config['setup']['systemInstruction'] = {'parts': [{'text': SHORT_PERSONA}]}
        if self.context:
            config['setup']['systemInstruction']['parts'].append({'text': self.context})
        config['setup']['inputAudioTranscription'] = {}
        return config

    def invalidate(self, status='MEMORY_SCOPE_CHANGED'):
        # Caller status cannot leak source text; only fixed known codes accepted.
        if status not in ('MEMORY_SCOPE_CHANGED', 'MEMORY_TRANSCRIPT_INVALID',
                          'MEMORY_TRANSCRIPT_LIMIT', 'MEMORY_END_UNSAFE'):
            status = 'MEMORY_SCOPE_CHANGED'
        self.buffer.clear()
        self.context = ''
        self.state = 'DISABLED'
        self.status = status

    def server_event(self, payload, *, safe):
        if not safe:
            self.invalidate()
            return
        if self.state != 'COLLECTING':
            return
        content = payload.get('serverContent')
        if content is None:
            return
        if not isinstance(content, dict):
            self.invalidate('MEMORY_TRANSCRIPT_INVALID')
            return
        if 'inputTranscription' not in content:
            return
        item = content['inputTranscription']
        # Ignore interimInputTranscription and outputTranscription completely.
        # Only independent final input fragments are source records. Their IDs
        # are NOT local utterance or response epoch IDs; no ordering is claimed.
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            self.invalidate('MEMORY_TRANSCRIPT_INVALID')
            return
        if not item['text']:
            return
        # Refuse offset-bearing fragments for now: mixing/revisions need a
        # separately tested ordering contract. Never guess source chronology.
        if 'startOffset' in item or 'endOffset' in item:
            self.invalidate('MEMORY_TRANSCRIPT_INVALID')
            return
        try:
            self.segments += 1
            if self.segments > MAX_TURNS:
                raise MemoryErrorCode('MEMORY_TRANSCRIPT_LIMIT')
            self.buffer.begin(self.segments)
            self.buffer.append(speaker=self.owner, text=item['text'])
            self.buffer.end()
        except MemoryErrorCode:
            self.invalidate('MEMORY_TRANSCRIPT_LIMIT')

    async def finish_candidates(self, *, reason, safe, send_once, timeout=20):
        """Return untrusted candidate tuples, never saves or claims saved.

        Runtime supplies one consented fixed-model sender. It must not retry or
        redirect. Only ordinary safe endings qualify; transport/safety errors
        don't persist partial conversations. No source content in public status.
        """
        if self.state != 'COLLECTING':
            return ()
        if not safe or reason not in ('MANUAL_STOP', 'SESSION_LIMIT', 'USER_IDLE'):
            self.invalidate('MEMORY_END_UNSAFE')
            return ()
        self.state = 'EXTRACTING'  # Consume the budget before awaiting anything.
        self.context = ''
        turns = self.buffer.take()
        if not turns:
            self.state = 'FINISHED'
            self.status = 'MEMORY_NO_INPUT'
            return ()
        try:
            request = extraction_request(turns)
            # Sources are independent received fragments. Never assert temporal
            # order or allow the extractor to infer a changed fact from position.
            request['system_instruction'] += (
                ' Source turn labels identify independent transcript segments, '
                'not chronological utterances. Do not infer temporal order or '
                'resolve conflicting preferences by label order. Save no facts '
                'requiring context from another segment to understand safely.')
            raw = await asyncio.wait_for(send_once(request), timeout)
            if self.state == 'DISABLED':
                return ()
            candidates = parse_candidates(raw, turns)
            result, self.skipped = safe_candidates(candidates, turns)
            self.state = 'FINISHED'
            self.status = 'MEMORY_CANDIDATES_READY'  # NOT MEMORY_SAVED
            return result
        except asyncio.CancelledError:
            self.state = 'DISABLED'
            self.status = 'MEMORY_EXTRACTION_CANCELLED'
            raise
        except Exception:
            self.state = 'DISABLED'
            self.status = 'MEMORY_EXTRACTION_FAILED'
            return ()
        finally:
            turns = request = raw = None


class MemoryDuplexTurn(DuplexTurn):
    """Opt-in class only; not constructed by the published natural runner."""
    def __init__(self, key, safe, memory):
        super().__init__(key, safe)
        self.memory = memory

    def setup(self):
        return self.memory.setup(super().setup())

    async def receive(self, timeout):
        payload = await super().receive(timeout)
        self.memory.server_event(payload, safe=self.safe())
        return payload  # Never swallow the same event's audio/control parts.
