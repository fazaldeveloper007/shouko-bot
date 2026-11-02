import aiohttp
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
import retrying

class Response:
    def __init__(self, status, body=None, headers=None):
        self.status, self.body, self.headers = status, body or {}, headers or {}
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def json(self, **kw): return self.body
class Session:
    def __init__(self, script): self.script=list(script); self.calls=[]
    def post(self, url, **kw):
        self.calls.append(url)
        item=self.script.pop(0)
        if isinstance(item, BaseException): raise item
        return item

class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, session, label='Gemini', payload=None):
        return await retrying.post_json(session,'https://test',label=label,headers={},payload=payload or {})
    async def test_initial_success(self):
        s=Session([Response(200,{'ok':True})]);self.assertEqual(await self.call(s),{'ok':True});self.assertEqual(len(s.calls),1)
    async def test_three_total_transients(self):
        s=Session([Response(503)]*4)
        with patch('retrying.asyncio.sleep',new=AsyncMock()):
            with self.assertRaises(retrying.RequestError): await self.call(s)
        self.assertEqual(len(s.calls),3)
    async def test_shared_failover_compat_budget(self):
        s=Session([Response(400,{'error':'thinkingConfig invalid'}),Response(401),Response(503),Response(200,{'never':True})])
        async with retrying.request_scope():
            with self.assertRaises(retrying.RequestError):
                await self.call(s,payload={'generationConfig':{'thinkingConfig':{}}})
            with self.assertRaises(retrying.RequestError): await self.call(s,label='Groq')
        self.assertEqual(len(s.calls),3)
    async def test_compat_and_503_then_success(self):
        s=Session([Response(400,{'error':'thinkingConfig invalid'}),Response(503),Response(200,{'ok':1})])
        with patch('retrying.asyncio.sleep',new=AsyncMock()):
            self.assertEqual(await self.call(s,payload={'generationConfig':{'thinkingConfig':{}}}),{'ok':1})
        self.assertEqual(len(s.calls),3)
    async def test_permanent_errors_no_retry(self):
        for status in [400,401,403,404,422]:
            s=Session([Response(status)])
            with self.assertRaises(retrying.RequestError): await self.call(s)
            self.assertEqual(len(s.calls),1)
    async def test_quota_no_retry(self):
        s=Session([Response(429,{'error':'quota exhausted'})])
        with self.assertRaises(retrying.RequestError): await self.call(s)
        self.assertEqual(len(s.calls),1)
    async def test_long_retry_after_no_early_retry(self):
        s=Session([Response(429,{}, {'Retry-After':'60'})])
        with patch('retrying.asyncio.sleep',new=AsyncMock()) as sleep:
            with self.assertRaises(retrying.RequestError): await self.call(s)
            sleep.assert_not_awaited()
        self.assertEqual(len(s.calls),1)
    async def test_within_retry_after_respected(self):
        s=Session([Response(429,{}, {'Retry-After':'3'}),Response(200,{'ok':1})])
        with patch('retrying.asyncio.sleep',new=AsyncMock()) as sleep:
            await self.call(s);sleep.assert_awaited_once_with(3)
    async def test_timeout_then_success(self):
        s=Session([asyncio.TimeoutError(),Response(200,{'ok':1})])
        with patch('retrying.asyncio.sleep',new=AsyncMock()): await self.call(s)
        self.assertEqual(len(s.calls),2)
    async def test_cancel_propagates_no_retry(self):
        s=Session([asyncio.CancelledError()])
        with self.assertRaises(asyncio.CancelledError): await self.call(s)
        self.assertEqual(len(s.calls),1)
    async def test_independent_background_scope(self):
        s=Session([Response(200,{'ok':1})]*4)
        async with retrying.request_scope():
            await self.call(s)
            async with retrying.request_scope():
                for _ in range(3): await self.call(s)
            self.assertEqual(retrying._current.get().used,1)
    async def test_expired_budget_makes_no_request(self):
        s=Session([])
        async with retrying.request_scope():
            retrying._current.get().deadline=0
            with self.assertRaises(retrying.RequestError): await self.call(s)
        self.assertEqual(len(s.calls),0)
    async def test_request_error_never_logs_body(self):
        self.assertNotIn('SECRET',str(retrying.RequestError('Gemini',403,{'error':'SECRET'})))

class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_four_step_pipeline_and_failover_budget(self):
        session=Session([Response(200, {'ok':1})]*4)
        @retrying.operation
        async def step():
            return await retrying.post_json(session,'x',label='T',headers={},payload={})
        async with retrying.request_scope():
            deadline=retrying._current.get().deadline
            for _ in range(4):
                self.assertEqual(await step(),{'ok':1})
            self.assertEqual(retrying._current.get().deadline,deadline)
        self.assertEqual(len(session.calls),4)
    async def test_same_generation_failover_stays_three(self):
        session=Session([Response(401),Response(503),Response(503),Response(200,{'never':1})])
        @retrying.operation
        async def generation():
            try:
                await retrying.post_json(session,'gemini',label='Gemini',headers={},payload={})
            except retrying.RequestError:
                return await retrying.post_json(session,'groq',label='Groq',headers={},payload={})
        with patch('retrying.asyncio.sleep',new=AsyncMock()):
            async with retrying.request_scope():
                with self.assertRaises(retrying.RequestError): await generation()
        self.assertEqual(len(session.calls),3)

class OutputValidationTests(unittest.IsolatedAsyncioTestCase):
    async def run_script(self,script,provider='Gemini'):
        session=Session(script)
        with patch('retrying.asyncio.sleep',new=AsyncMock()):
            result=await retrying.post_json(session,'x',label=provider,headers={},payload={},validate=retrying.generation_validator(provider))
        return result,session
    def gemini(self,text='hello',reason='STOP'):
        return {'candidates':[{'finishReason':reason,'content':{'parts':[{'text':text}]}}]}
    async def test_empty_then_valid(self):
        _,s=await self.run_script([Response(200,{}),Response(200,self.gemini())]);self.assertEqual(len(s.calls),2)
    async def test_reported_malformed_response_finish_reason_retried(self):
        _,s=await self.run_script([Response(200,self.gemini('',reason='MALFORMED_RESPONSE')),Response(200,self.gemini())]);self.assertEqual(len(s.calls),2)
    async def test_three_empty_only(self):
        s=Session([Response(200,{})]*4)
        with patch('retrying.asyncio.sleep',new=AsyncMock()):
            with self.assertRaises(retrying.RequestError):
                await retrying.post_json(s,'x',label='Gemini',headers={},payload={},validate=retrying.generation_validator('Gemini'))
        self.assertEqual(len(s.calls),3)
    async def test_block_never_retried(self):
        for data in ({'promptFeedback':{'blockReason':'SAFETY'}},self.gemini('',reason='SAFETY'),self.gemini('',reason='RECITATION')):
            s=Session([Response(200,data)])
            with self.assertRaises(retrying.RequestError) as caught:
                await retrying.post_json(s,'x',label='Gemini',headers={},payload={},validate=retrying.generation_validator('Gemini'))
            self.assertEqual(caught.exception.reason,'blocked');self.assertEqual(len(s.calls),1)
    async def test_malformed_groq_empty_then_valid(self):
        _,s=await self.run_script([Response(200,{'choices':[]}),Response(200,{'choices':[{'message':{'content':''}}]}),Response(200,{'choices':[{'message':{'content':'hello'}}]})],'Groq')
        self.assertEqual(len(s.calls),3)
    async def test_mixed_http_and_empty_share_cap(self):
        _,s=await self.run_script([Response(503),Response(200,{}),Response(200,self.gemini())]);self.assertEqual(len(s.calls),3)
    async def test_diagnostics_no_body_or_headers(self):
        with self.assertLogs('kristy.retry',level='INFO') as logs:
            await self.run_script([Response(200,{'secret':'TOKEN_MARKER'}),Response(200,self.gemini())])
        text=' '.join(logs.output);self.assertIn('attempt=2/3',text);self.assertIn('malformed_output',text);self.assertNotIn('TOKEN_MARKER',text)

class TransportDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def call(self,s,**kw):
        async with retrying.request_scope():
            return await retrying.post_json(s,'https://example.invalid',label='Gemini',headers={},payload={},**kw)
    async def test_timeout_logs_error_class(self):
        s=Session([asyncio.TimeoutError(),Response(200,{'ok':1})])
        with patch('retrying.asyncio.sleep',new=AsyncMock()):
            with self.assertLogs('kristy.retry',level='INFO') as logs:
                await self.call(s)
        self.assertTrue(any('reason=transport error=TimeoutError' in line for line in logs.output))
    async def test_connector_error_logs_class_and_errno(self):
        err=aiohttp.ClientConnectorError(connection_key=None,os_error=OSError(111,'refused'))
        s=Session([err,Response(200,{'ok':1})])
        with patch('retrying.asyncio.sleep',new=AsyncMock()):
            with self.assertLogs('kristy.retry',level='INFO') as logs:
                await self.call(s)
        self.assertTrue(any('reason=transport error=ClientConnectorError(111)' in line for line in logs.output))
