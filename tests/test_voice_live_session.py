"""Session protocol and actual pinned receiver/player. No Google or Discord calls."""
import asyncio
import base64
import copy
import json
import pathlib
import shutil
import subprocess
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_voice_live import Socket, audio_event
from test_voice_live_streaming import HARNESS
from voice_live.session_protocol import SessionTurn
from voice_live.gemini import LiveError, SETUP
from voice_live import session as runner

ROOT = pathlib.Path(__file__).resolve().parents[1]


class SessionProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_model_voice_private_setup_and_default_unchanged(self):
        original = copy.deepcopy(SETUP)
        turn = SessionTurn('DUMMY', lambda: True)
        config = turn.setup()['setup']
        self.assertEqual(config['model'], 'models/gemini-3.1-flash-live-preview')
        self.assertEqual(config['generationConfig']['speechConfig']['voiceConfig']['prebuiltVoiceConfig']['voiceName'], 'Sulafat')
        self.assertEqual(config['generationConfig']['maxOutputTokens'], 256)
        for absent in ('sessionResumption', 'contextWindowCompression', 'tools', 'historyConfig', 'inputAudioTranscription', 'outputAudioTranscription'):
            self.assertNotIn(absent, config)
        self.assertEqual(SETUP, original)

    async def test_two_turns_same_socket_delayed_provider_completion_barrier(self):
        turn = SessionTurn('DUMMY', lambda: True)
        turn.ws = Socket([audio_event(bytes(960), complete=False),
                          {'serverContent': {'generationComplete': True}},
                          {'serverContent': {'turnComplete': True}}])
        turn.input_ended = True
        send = AsyncMock()
        await turn.output(send)
        self.assertTrue(turn.provider_complete)
        self.assertEqual(send.call_args.args[0], {'type': 'output_end'})
        await turn.begin_next()
        self.assertFalse(turn.provider_complete)
        self.assertEqual(turn.output_bytes, 0)
        self.assertEqual(turn.ws.sent[-1], {'realtimeInput': {'activityStart': {}}})
        await turn.input_audio(base64.b64encode(bytes(640)).decode())
        await turn.end_input()
        turn.ws.events.append(audio_event(bytes(1920)))
        await turn.output(send)
        self.assertEqual(turn.output_bytes, 1920)
        self.assertTrue(turn.provider_complete)

    async def test_no_next_activity_before_provider_complete(self):
        turn = SessionTurn('DUMMY', lambda: True)
        turn.ws = Socket()
        with self.assertRaisesRegex(LiveError, 'PROVIDER_TURN_NOT_COMPLETE'):
            await turn.begin_next()
        self.assertEqual(turn.ws.sent, [])

    async def test_missing_turn_complete_audio_after_generation_and_goaway_stop(self):
        for tail, code in [({'goAway': {'timeLeft': '1s'}}, 'GOOGLE_UNEXPECTED_ACTION'),
                           (audio_event(bytes(960)), 'GOOGLE_AUDIO_AFTER_GENERATION'),
                           ({'serverContent': {'interrupted': True}}, 'GOOGLE_INTERRUPTED')]:
            turn = SessionTurn('DUMMY', lambda: True)
            turn.input_ended = True
            turn.ws = Socket([audio_event(bytes(960), complete=False),
                              {'serverContent': {'generationComplete': True}}, tail])
            with self.assertRaisesRegex(LiveError, code):
                await turn.output(AsyncMock())
            self.assertFalse(turn.provider_complete)
        turn = SessionTurn('DUMMY', lambda: True)
        turn.input_ended = True
        turn.receive = AsyncMock(side_effect=[{'serverContent': {'generationComplete': True}}, asyncio.TimeoutError()])
        with self.assertRaisesRegex(LiveError, 'GOOGLE_TURN_COMPLETE_TIMEOUT'):
            await turn.output(AsyncMock())

    async def test_per_turn_cap_ends_session_not_silent_context_continuation(self):
        turn = SessionTurn('DUMMY', lambda: True)
        turn.input_ended = True
        turn.ws = Socket([audio_event(bytes(490000))])
        with self.assertRaisesRegex(LiveError, 'OUTPUT_LIMIT'):
            await turn.output(AsyncMock())
        self.assertEqual(turn.output_bytes, 480000)

    async def test_gate_before_credential_or_client(self):
        with patch.object(runner, 'existing_token', side_effect=AssertionError('credential')):
            with self.assertRaisesRegex(LiveError, 'EXISTING_KEY_FREE_TIER_NOT_CONFIRMED'):
                await runner.run(42)

    async def test_exchange_rearms_only_after_both_barriers_and_closes(self):
        entered = asyncio.Event()
        turn = types.SimpleNamespace(open=AsyncMock(), close=AsyncMock(), input_audio=AsyncMock(),
            end_input=AsyncMock(), input_bytes=640, begin_next=AsyncMock())
        async def provider(send):
            entered.set()
        turn.output = provider
        bridge = types.SimpleNamespace(safe=lambda: True, turn_id=0, future=lambda k: None,
            wait=AsyncMock(), audio=asyncio.Queue(), turn_done=asyncio.Queue(), write=AsyncMock())
        bridge.audio.put_nowait({'type': 'input_end', 'turn': 1})
        with patch.object(runner, 'SessionTurn', return_value=turn):
            task = asyncio.create_task(runner.exchange(bridge, 'DUMMY'))
            await entered.wait()
            await asyncio.sleep(0)
            self.assertEqual(bridge.turn_id, 1)
            turn.begin_next.assert_not_awaited()
            bridge.turn_done.put_nowait({'type': 'turn_done', 'turn': 1})
            for _ in range(10):
                await asyncio.sleep(0)
                if bridge.turn_id == 2:
                    break
            self.assertEqual(bridge.turn_id, 2)
            turn.begin_next.assert_awaited_once()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        turn.close.assert_awaited_once()


# Reuse the real encrypted RTP/VoiceReceiver/AudioPlayer fixture, replacing
# only its one-turn scenario body. The worker under test is the session worker.
PREFIX = HARNESS[:HARNESS.index('(async()=>{')].replace("root+'/voice_live/worker.cjs'", "root+'/voice_live/worker_session.cjs'")
# Initial init is identical. Turns must carry explicit sequence IDs.
BODY = r'''
(async()=>{
 await new Promise(setImmediate);
 const encoder=new O(48000,2,O.Application.AUDIO),pcm=Buffer.alloc(3840),mono=Buffer.alloc(9600);
 for(let i=0;i<pcm.length;i+=2)pcm.writeInt16LE(Math.round(3000*Math.sin(i/2*440*2*Math.PI/48000)),i);
 for(let i=0;i<mono.length;i+=2)mono.writeInt16LE(Math.round(3000*Math.sin(i/2*440*2*Math.PI/24000)),i);
 async function capture(turn){
  send({type:'listen',turn});
  for(let n=0;n<10;n++){
   const opus=encoder.encode(pcm,960),header=Buffer.alloc(12),nonce=Buffer.alloc(12),suffix=Buffer.alloc(4);
   header[0]=0x80;header[1]=0x78;header.writeUInt16BE(n+turn*100,2);header.writeUInt32BE((n+turn*100)*960,4);header.writeUInt32BE(ssrc,8);nonce.writeUInt32BE(n+turn*100,0);suffix.writeUInt32BE(n+turn*100);
   const cipher=crypto.createCipheriv('aes-256-gcm',key,nonce);cipher.setAAD(header);
   conn.receiver.onUdpMessage(Buffer.concat([header,cipher.update(opus),cipher.final(),cipher.getAuthTag(),suffix]));
   await new Promise(setImmediate);
  }
  timers.filter(t=>t.ms===8000&&!t.cancelled).at(-1).fn();
 }
 async function reply(turn){
  const data=mode==='tiny'?Buffer.alloc(2):mode==='silent'?Buffer.alloc(9600):mono;
  send({type:'output',turn,data:data.toString('base64')});
  send({type:'output_end',turn});
  await new Promise(resolve=>setTimeout(resolve,450));
 }
 await capture(1);
 if(mode==='overlap'){send({type:'listen',turn:2});}
 else if(mode==='stale'){send({type:'output',turn:0,data:mono.toString('base64')});}
 else if(mode==='dave'){encrypted=false;send({type:'output',turn:1,data:mono.toString('base64')});}
 else if(mode==='other'){conn.receiver.speaking.emit('start','500000000000000');}
 else if(mode==='output_timeout'){send({type:'output',turn:1,data:mono.toString('base64')});await new Promise(r=>setTimeout(r,90));timers.filter(t=>t.ms===10000&&!t.cancelled).at(-1).fn();}
 else if(mode==='capture_stop'){send({type:'stop'});}
 else {
  await reply(1);
  if(!['tiny','silent'].includes(mode)){
   assert.equal(messages.filter(m=>m.type==='turn_done').length,1);
   assert.equal(destroyed,false);assert.equal(conn.receiver.subscriptions.size,0);
   await capture(2);await reply(2);
   assert.equal(messages.filter(m=>m.type==='turn_done').length,2);
   assert.equal(messages.filter(m=>m.type==='playing').length,2);
   assert.deepEqual(subscriptions,[target,target]);
   assert.equal(messages.filter(m=>m.type==='audio').length,20);
   if(mode==='deadline')timers.find(t=>t.ms===600000).fn();
   else send({type:'stop'});
  }
 }
 await new Promise(setImmediate);
 const result=messages.find(m=>m.type==='result');assert.ok(result);assert.equal(destroyed,true);
 console.log(JSON.stringify({result,types:messages.map(m=>m.type),nonzero,dispatched}));
 encoder.delete();outputDecoder.delete();key.fill(0);pcm.fill(0);mono.fill(0);process.exit(0);
})().catch(e=>{console.error(e.stack);console.error(JSON.stringify(messages));process.exit(1);});
'''


class SessionStreaming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('node') or not (ROOT/'voice_probe/node_modules/opusscript').exists():
            raise unittest.SkipTest('existing dependencies needed')

    def test_actual_two_turn_player_pipeline_and_session_stops(self):
        for mode, code, turns in [('normal','STOPPED',2),('deadline','SESSION_LIMIT',2),
                                 ('overlap','INVALID_COMMAND',0),('stale','INVALID_COMMAND',0),
                                 ('dave','DAVE_NOT_READY',0),('other','OTHER_SPEAKER',0),
                                 ('tiny','GOOGLE_AUDIO_TOO_SHORT',0),('silent','GOOGLE_AUDIO_SILENT',0),
                                 ('output_timeout','OUTPUT_LIMIT',0),('capture_stop','STOPPED',0)]:
            with self.subTest(mode=mode):
                proc=subprocess.run(['node','-e',PREFIX+BODY,str(ROOT),mode],capture_output=True,text=True,timeout=6)
                self.assertEqual(proc.returncode,0,proc.stderr)
                data=json.loads(proc.stdout)
                self.assertEqual(data['result']['code'],code)
                self.assertEqual(data['result']['turnsCompleted'],turns)
                if turns:
                    self.assertEqual(data['result']['inputBytes'],12800)
                    self.assertEqual(data['result']['outputBytes'],19200)
                    self.assertGreater(data['nonzero'],0)

class RunnerSessionGates(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, foreign=False, answer='START', safety=False):
        from test_voice_live import RunnerGates
        guild=RunnerGates().guild(foreign=foreign)
        callbacks={}
        def event(fn): callbacks[fn.__name__]=fn;return fn
        async def start(*a,**kw):
            self.assertFalse(kw['reconnect'])
            await callbacks['on_ready']()
        client=types.SimpleNamespace(event=event,start=start,close=AsyncMock(),user=types.SimpleNamespace(id=99),guilds=[guild])
        process=types.SimpleNamespace(wait=AsyncMock(return_value=0),returncode=0)
        bridge=types.SimpleNamespace(p=None,state='ready',result=asyncio.get_running_loop().create_future(),safe=lambda:False,write=AsyncMock())
        async def disconnect(*a,**kw):
            if not bridge.result.done():bridge.result.set_result({'code':'STOPPED','turnsCompleted':2})
            return True
        bridge.disconnect=disconnect
        n=0
        async def console():
            nonlocal n
            n+=1
            if n==1:return answer
            await asyncio.Event().wait()
        async def wait_for_session(tasks, **kw):
            self.assertEqual(kw['timeout'],600)
            self.assertEqual(kw['return_when'],asyncio.FIRST_COMPLETED)
            if safety:
                await callbacks['on_voice_state_update'](None,None,None)
                return ({tasks[2]},set())
            return (set(),set(tasks))
        async def connect(**kw):
            self.assertFalse(kw['reconnect']);self.assertFalse(kw['self_mute'])
        with patch.object(runner.sys.stdin,'isatty',return_value=True),patch.object(runner,'existing_token',return_value='DUMMY'),patch.object(runner,'existing_gemini_key',return_value='DUMMY'),patch.object(runner.discord,'Client',return_value=client),patch.object(runner.asyncio,'create_subprocess_exec',AsyncMock(return_value=process)),patch.object(runner,'console_line',console),patch.object(runner,'LiveBridge',return_value=bridge) as factory,patch.object(runner,'exchange',AsyncMock()) as exchange,patch.object(runner.asyncio,'wait',wait_for_session),patch.object(runner.discord.VoiceChannel,'connect',connect),patch('builtins.print') as output:
            await runner.run(42,confirmed_free_tier=True)
            if foreign or answer!='START':
                factory.assert_not_called();exchange.assert_not_awaited()
            else:
                factory.assert_called_once()
                text=str(output.call_args_list)
                self.assertIn('SAFETY_STOP' if safety else 'SESSION_LIMIT',text)
                self.assertIn('LEAVE_CONFIRMED',text)
                self.assertIn('Sulafat',text)
                self.assertIn('gemini-3.1-flash-live-preview',text)
                if safety:bridge.write.assert_any_await({'type':'stop'})
            client.close.assert_awaited_once()

    async def test_deadline_includes_setup_and_safety_stop(self):
        await self.exercise()
        await self.exercise(safety=True)

    async def test_foreign_member_and_cancel_prevent_session(self):
        await self.exercise(foreign=True)
        await self.exercise(answer='CANCEL')
