"""No network. Finite Live protocol, privacy gates and result projection."""
import asyncio
import base64
import copy
import importlib
import json
import pathlib
import types
import unittest
from unittest.mock import AsyncMock, patch

from voice_live.gemini import (NativeTurn, LiveError, MODEL, ENDPOINT,
                               SETUP, COMPARISON_MODEL, ALLOWED_MODELS, setup_for, require_free_tier, decode_audio)
from voice_live import run as runner


class Socket:
    def __init__(self, events=()):
        self.events = list(events)
        self.sent = []
        self.closed = False

    async def send_json(self, value):
        self.sent.append(copy.deepcopy(value))

    async def receive(self):
        from aiohttp import WSMsgType
        value = self.events.pop(0)
        return types.SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(value))

    async def close(self):
        self.closed = True


def audio_event(data, complete=True, parts=1):
    return {'serverContent': {'modelTurn': {'parts': [
        {'inlineData': {'mimeType': 'audio/pcm;rate=24000',
                        'data': base64.b64encode(data).decode()}} for _ in range(parts)]},
        'turnComplete': complete}}


class ExplicitModelComparison(unittest.IsolatedAsyncioTestCase):
    def test_default_unchanged_and_setup_isolated(self):
        self.assertEqual(MODEL, 'gemini-3.8-live')
        self.assertEqual(ALLOWED_MODELS, (MODEL, 'gemini-3.1-flash-live-preview'))
        expected = copy.deepcopy(SETUP)
        expected['setup']['model'] = 'models/' + COMPARISON_MODEL
        actual = setup_for(COMPARISON_MODEL)
        self.assertEqual(actual, expected)
        actual['setup']['generationConfig']['maxOutputTokens'] = 999
        self.assertEqual(setup_for(COMPARISON_MODEL), expected)
        self.assertEqual(SETUP['setup']['model'], 'models/' + MODEL)

    async def test_unapproved_model_blocked_before_credentials(self):
        with patch.object(runner, 'existing_token', side_effect=AssertionError('credential read')):
            with self.assertRaisesRegex(LiveError, 'MODEL_NOT_APPROVED'):
                await runner.run(42, confirmed_free_tier=True, model='unapproved')

    async def test_comparison_still_needs_free_tier_confirmation(self):
        with patch.object(runner, 'existing_token', side_effect=AssertionError('credential read')):
            with self.assertRaisesRegex(LiveError, 'EXISTING_KEY_FREE_TIER_NOT_CONFIRMED'):
                await runner.run(42, model=COMPARISON_MODEL)

    async def test_each_model_uses_exact_setup_no_fallback_on_rejection(self):
        for model in ALLOWED_MODELS:
            for accepted in (True, False):
                with self.subTest(model=model, accepted=accepted):
                    socket = Socket([{'setupComplete': {}} if accepted else {'error': {'code': 403}}])
                    session = types.SimpleNamespace(ws_connect=AsyncMock(return_value=socket), close=AsyncMock())
                    with patch('voice_live.gemini.aiohttp.ClientSession', return_value=session):
                        turn = NativeTurn('DUMMY', lambda: True, model=model)
                        if accepted:
                            await turn.open()
                        else:
                            with self.assertRaisesRegex(LiveError, 'GOOGLE_API_REJECTED'):
                                await turn.open()
                        self.assertEqual(socket.sent[0], setup_for(model))
                        self.assertEqual(session.ws_connect.await_count, 1)
                        self.assertEqual(len([x for x in socket.sent if 'setup' in x]), 1)
                        await turn.close()

    async def test_exchange_passes_explicit_model_without_substitution(self):
        for model in ALLOWED_MODELS:
            turn = types.SimpleNamespace(open=AsyncMock(side_effect=LiveError('GOOGLE_SETUP_REJECTED')), close=AsyncMock())
            bridge = types.SimpleNamespace(safe=lambda: True)
            with patch.object(runner, 'NativeTurn', return_value=turn) as factory:
                with self.assertRaisesRegex(LiveError, 'GOOGLE_SETUP_REJECTED'):
                    await runner.exchange(bridge, 'DUMMY', model=model)
                factory.assert_called_once_with('DUMMY', bridge.safe, model=model)
                turn.close.assert_awaited_once()


class Guards(unittest.TestCase):
    def test_only_explicit_matching_free_project_assertion(self):
        for value in (None, '', 'free', False, 'old-project'):
            with self.assertRaisesRegex(LiveError, 'EXISTING_KEY_FREE_TIER_NOT_CONFIRMED'):
                require_free_tier(value)
        require_free_tier(True)

    def test_minimal_setup_no_history_tools_transcriptions_or_old_model_flags(self):
        self.assertEqual(SETUP['setup']['model'], 'models/' + MODEL)
        self.assertEqual(SETUP['setup']['generationConfig']['responseModalities'], ['AUDIO'])
        for forbidden in ('tools', 'inputAudioTranscription', 'outputAudioTranscription',
                          'proactivity', 'thinkingLevel', 'cachedContent'):
            self.assertNotIn('\"'+forbidden+'\":', json.dumps(SETUP))
        self.assertNotIn('key=', ENDPOINT)

    def test_malformed_or_oversized_audio_fails_closed(self):
        for value in ('!!', 'A', base64.b64encode(b'x').decode(), 'a'*1400004, None):
            with self.assertRaises(LiveError):
                decode_audio(value)

    def test_fixed_public_error_redaction(self):
        with patch('builtins.print') as out:
            runner.report_error(ValueError('PRIVATE_RAW_SERVER_TEXT'))
        self.assertNotIn('PRIVATE', str(out.call_args))

    def test_metrics_never_forward_audio_text_or_key(self):
        result=runner.public_result({'code':'COMPLETE','data':'PRIVATE_AUDIO','key':'PRIVATE_KEY','text':'PRIVATE_TEXT'})
        self.assertNotIn('PRIVATE', json.dumps(result))


class Protocol(unittest.IsolatedAsyncioTestCase):
    async def test_free_gate_precedes_credentials_or_client_construction(self):
        with patch.object(runner, 'existing_token', side_effect=AssertionError('read key')), patch.object(runner.discord, 'Client', side_effect=AssertionError('network')):
            with self.assertRaises(LiveError):
                await runner.run(1217032327260274773)

    async def test_header_auth_setup_and_manual_turn_no_retry(self):
        socket=Socket([{'setupComplete':{}}])
        session=types.SimpleNamespace(ws_connect=AsyncMock(return_value=socket), close=AsyncMock())
        with patch('voice_live.gemini.aiohttp.ClientSession', return_value=session):
            turn=NativeTurn('DUMMY_NOT_A_KEY', lambda:True)
            await turn.open()
            call=session.ws_connect.call_args
            self.assertEqual(call.args[0],ENDPOINT)
            self.assertEqual(call.kwargs['headers'], {'x-goog-api-key':'DUMMY_NOT_A_KEY'})
            self.assertNotIn('DUMMY',call.args[0])
            await turn.input_audio(base64.b64encode(bytes(640)).decode())
            await turn.end_input()
            self.assertEqual(socket.sent[0], SETUP)
            self.assertEqual(socket.sent[1], {'realtimeInput':{'activityStart':{}}})
            self.assertEqual(socket.sent[-1], {'realtimeInput':{'activityEnd':{}}})
            self.assertNotIn('audioStreamEnd',json.dumps(socket.sent))
            await turn.close()
            self.assertTrue(socket.closed)
            self.assertIsNone(turn.key)
            self.assertEqual(session.ws_connect.await_count,1)

    async def test_all_response_parts_processed_no_transcripts_forwarded(self):
        turn=NativeTurn('DUMMY',lambda:True);turn.input_ended=True
        event=audio_event(bytes(960),parts=2)
        event['serverContent']['modelTurn']['parts'].insert(0,{'text':'DO_NOT_FORWARD'})
        turn.ws=Socket([event]);send=AsyncMock()
        await turn.output(send)
        self.assertEqual(turn.output_bytes,1920)
        self.assertEqual(send.await_count,3)
        self.assertEqual(send.call_args.args[0], {'type':'output_end'})
        self.assertNotIn('DO_NOT_FORWARD',str(send.call_args_list))

    async def test_output_cap_even_if_server_sends_longer(self):
        turn=NativeTurn('DUMMY',lambda:True);turn.input_ended=True
        turn.ws=Socket([audio_event(bytes(490000))]);send=AsyncMock()
        await turn.output(send)
        self.assertEqual(turn.output_bytes,480000)
        outputs=[c.args[0] for c in send.call_args_list if c.args[0]['type']=='output']
        self.assertEqual(sum(len(base64.b64decode(x['data'])) for x in outputs),480000)
        self.assertTrue(all(len(x['data'])<=16000 for x in outputs))

    async def test_input_bytes_cannot_exceed_eight_seconds(self):
        turn=NativeTurn('DUMMY',lambda:True);turn.ws=Socket()
        turn.input_bytes=255600
        with self.assertRaisesRegex(LiveError,'INPUT_BYTE_LIMIT'):
            await turn.input_audio(base64.b64encode(bytes(640)).decode())
        self.assertEqual(turn.ws.sent,[])

    async def test_membership_loss_prevents_upload_and_playback(self):
        turn=NativeTurn('DUMMY',lambda:False);turn.ws=Socket([audio_event(bytes(640))])
        with self.assertRaisesRegex(LiveError,'MEMBERSHIP_CHANGED'):
            await turn.input_audio(base64.b64encode(bytes(640)).decode())
        turn.input_ended=True;send=AsyncMock()
        with self.assertRaisesRegex(LiveError,'MEMBERSHIP_CHANGED'):
            await turn.output(send)
        send.assert_not_awaited()

    async def test_bad_format_goaway_tool_and_error_fail_without_raw_messages(self):
        events=[{'error':{'message':'PRIVATE','code':429}}, {'goAway':{'timeLeft':'10s'}},
                {'toolCall':{'functionCalls':[]}}, {'serverContent':{'modelTurn':{'parts':[{'inlineData':{'mimeType':'audio/wav','data':'AAAA'}}]}}}]
        for event in events:
            turn=NativeTurn('DUMMY',lambda:True);turn.ws=Socket([event]);turn.input_ended=True
            with self.assertRaises(LiveError) as raised:
                await turn.output(AsyncMock())
            self.assertNotIn('PRIVATE',str(raised.exception))

    async def test_setup_reject_closes_and_does_not_retry(self):
        socket=Socket([{'error':{'message':'PRIVATE','code':403}}])
        session=types.SimpleNamespace(ws_connect=AsyncMock(return_value=socket),close=AsyncMock())
        with patch('voice_live.gemini.aiohttp.ClientSession',return_value=session):
            turn=NativeTurn('DUMMY',lambda:True)
            with self.assertRaises(LiveError):
                await turn.open()
            self.assertTrue(socket.closed)
            session.close.assert_awaited_once()
            self.assertEqual(session.ws_connect.await_count,1)

class RunnerGates(unittest.IsolatedAsyncioTestCase):
    def guild(self, requester=True, foreign=False, bot_elsewhere=False):
        # Actual discord.py public channel maps, not invented Guild attributes.
        guild=object.__new__(runner.discord.Guild);guild.id=100;guild._channels={};guild._voice_states={}
        for cid in (10,11):
            ch=object.__new__(runner.discord.VoiceChannel);ch.id=cid;ch.guild=guild;ch.position=cid;guild._channels[cid]=ch
        if requester:guild._voice_states[42]=types.SimpleNamespace(channel=guild._channels[10])
        if foreign:guild._voice_states[77]=types.SimpleNamespace(channel=guild._channels[10])
        if bot_elsewhere:guild._voice_states[99]=types.SimpleNamespace(channel=guild._channels[11])
        return guild

    async def exercise(self, foreign=False, bot_elsewhere=False, answer='CANCEL'):
        guild=self.guild(foreign=foreign,bot_elsewhere=bot_elsewhere);callbacks={}
        def event(fn):callbacks[fn.__name__]=fn;return fn
        async def start(*a,**kw):await callbacks['on_ready']()
        client=types.SimpleNamespace(event=event,start=start,close=AsyncMock(),user=types.SimpleNamespace(id=99),guilds=[guild])
        process=types.SimpleNamespace(wait=AsyncMock(return_value=0),returncode=0)
        with patch.object(runner.sys.stdin,'isatty',return_value=True),patch.object(runner,'existing_token',return_value='DUMMY'),patch.object(runner,'existing_gemini_key',return_value='DUMMY'),patch.object(runner.discord,'Client',return_value=client),patch.object(runner.asyncio,'create_subprocess_exec',AsyncMock(return_value=process)),patch.object(runner,'console_line',AsyncMock(return_value=answer)) as console,patch.object(runner,'LiveBridge') as bridge,patch.object(runner,'NativeTurn') as google,patch('builtins.print') as output:
            await runner.run(42,confirmed_free_tier=True)
            bridge.assert_not_called();google.assert_not_called();client.close.assert_awaited_once()
            if foreign or bot_elsewhere:console.assert_not_awaited()
            else:console.assert_awaited_once()
            self.assertIn('LIVE_JOIN_NOT_REQUESTED',str(output.call_args_list))

    async def test_actual_api_cancel_has_no_voice_or_google_session(self):
        await self.exercise()

    async def test_other_occupant_or_connected_bot_prevents_start(self):
        await self.exercise(foreign=True)
        await self.exercise(bot_elsewhere=True)

    async def test_exchange_always_closes_google_on_stop(self):
        opened=asyncio.Event()
        async def open_():opened.set();await asyncio.Event().wait()
        turn=types.SimpleNamespace(open=open_,close=AsyncMock())
        bridge=types.SimpleNamespace(safe=lambda:True)
        with patch.object(runner,'NativeTurn',return_value=turn):
            task=asyncio.create_task(runner.exchange(bridge,'DUMMY'))
            await opened.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        turn.close.assert_awaited_once()

    async def test_disconnect_stops_output_before_waiting_for_leave(self):
        guild=self.guild();abort=asyncio.Event()
        client=types.SimpleNamespace(user=types.SimpleNamespace(id=99))
        bridge=runner.LiveBridge(client,guild._channels[10],42,abort)
        bridge.p=types.SimpleNamespace(returncode=None)
        order=[]
        async def write(value):order.append(value['type'])
        async def parent(*a,**kw):order.append('leave');return True
        bridge.write=write
        with patch.object(runner.Bridge,'disconnect',parent):
            self.assertTrue(await bridge.disconnect(force=True))
        self.assertEqual(order,['stop','leave']);self.assertTrue(abort.is_set())

class GenerationLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_generation_complete_finishes_without_waiting_for_turn_complete(self):
        turn=NativeTurn('DUMMY',lambda:True);turn.input_ended=True
        event=audio_event(bytes(960),complete=False)
        turn.ws=Socket([event,{'serverContent':{'generationComplete':True}}])
        send=AsyncMock();await turn.output(send)
        self.assertEqual(send.call_args.args[0],{'type':'output_end'})
        self.assertEqual(turn.ws.events,[])

    async def test_tiny_audio_then_generation_complete_is_forwarded_not_invented(self):
        turn=NativeTurn('DUMMY',lambda:True);turn.input_ended=True
        event=audio_event(bytes(2),complete=False)
        event['serverContent']['generationComplete']=True
        turn.ws=Socket([event]);send=AsyncMock();await turn.output(send)
        self.assertEqual(turn.output_bytes,2)
        self.assertEqual(send.await_count,2)
        self.assertEqual(base64.b64decode(send.call_args_list[0].args[0]['data']),bytes(2))

    async def test_idle_receive_timeout_has_specific_fixed_code(self):
        turn=NativeTurn('DUMMY',lambda:True);turn.input_ended=True
        turn.receive=AsyncMock(side_effect=asyncio.TimeoutError())
        with self.assertRaisesRegex(LiveError,'GOOGLE_RESPONSE_IDLE_TIMEOUT'):
            await turn.output(AsyncMock())

    async def test_generation_complete_with_multiple_parts_processes_all_audio_first(self):
        turn=NativeTurn('DUMMY',lambda:True);turn.input_ended=True
        event=audio_event(bytes(960),complete=False,parts=2)
        event['serverContent']['generationComplete']=True
        turn.ws=Socket([event]);send=AsyncMock();await turn.output(send)
        self.assertEqual(turn.output_bytes,1920)
        self.assertEqual([c.args[0]['type'] for c in send.call_args_list],['output','output','output_end'])


class Outcomes(unittest.TestCase):
    def test_cleanup_stop_does_not_hide_upstream_failure(self):
        result=runner.public_result({'code':'STOPPED','outputBytes':2},'GOOGLE_RESPONSE_IDLE_TIMEOUT')
        self.assertEqual(result['code'],'GOOGLE_RESPONSE_IDLE_TIMEOUT')
        self.assertEqual(result['workerCode'],'STOPPED')
        self.assertEqual(result['outputBytes'],2)

    def test_overall_limit_distinct_from_idle_and_manual(self):
        for outcome in ('OVERALL_LIMIT','GOOGLE_RESPONSE_IDLE_TIMEOUT','MANUAL_STOP','SAFETY_STOP'):
            self.assertEqual(runner.public_result({'code':'STOPPED'},outcome)['code'],outcome)
