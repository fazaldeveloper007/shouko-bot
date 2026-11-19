"""Local-gated full-duplex tests. Synthetic audio/fake sockets, never cloud calls."""
import asyncio
import base64
import json
import pathlib
import shutil
import subprocess
import types
import unittest
from unittest.mock import AsyncMock, patch
from test_voice_live import Socket, audio_event
from test_voice_live_streaming import HARNESS
from voice_live.duplex_protocol import DuplexTurn, DuplexFlow
from voice_live.gemini import LiveError
from voice_live import duplex as runner

ROOT=pathlib.Path(__file__).resolve().parents[1]


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def flow(self):
        turn=DuplexTurn('DUMMY',lambda:True)
        turn.ws=Socket()
        bridge=types.SimpleNamespace(audio=asyncio.Queue(),write=AsyncMock())
        return DuplexFlow(turn,bridge)

    async def stop(self, task):
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task

    async def spin(self, condition):
        for _ in range(5000):
            if condition():return
            await asyncio.sleep(0)
        self.fail('expected state not reached')

    async def test_setup_has_no_empty_activity_or_idle_upload(self):
        turn=DuplexTurn('DUMMY',lambda:True)
        socket=Socket([{'setupComplete':{}}])
        session=types.SimpleNamespace(ws_connect=AsyncMock(return_value=socket),close=AsyncMock())
        with patch('voice_live.gemini.aiohttp.ClientSession',return_value=session):
            await turn.open()
            self.assertEqual(len(socket.sent),1)
            config=socket.sent[0]['setup']
            self.assertEqual(config['realtimeInputConfig'],{'automaticActivityDetection':{'disabled':True},'activityHandling':'START_OF_ACTIVITY_INTERRUPTS'})
            self.assertEqual(config['model'],'models/gemini-3.1-flash-live-preview')
            self.assertEqual(config['generationConfig']['speechConfig']['voiceConfig']['prebuiltVoiceConfig']['voiceName'],'Sulafat')
            await turn.close()
        flow=self.flow();task=asyncio.create_task(flow.send_input())
        await asyncio.sleep(.02)
        self.assertEqual(flow.turn.ws.sent,[])
        await self.stop(task)

    async def test_manual_input_longer_than_old_eight_seconds_and_precise_cap(self):
        flow=self.flow();task=asyncio.create_task(flow.send_input())
        flow.bridge.audio.put_nowait({'type':'speech_start','epoch':1})
        for _ in range(450):flow.bridge.audio.put_nowait({'type':'audio','epoch':1,'data':base64.b64encode(bytes(640)).decode()})
        flow.bridge.audio.put_nowait({'type':'speech_end','epoch':1})
        await self.spin(lambda:flow.pending==1)
        self.assertEqual(flow.input_bytes,288000)
        self.assertEqual(flow.turn.ws.sent[0],{'realtimeInput':{'activityStart':{}}})
        await self.spin(lambda:'activityEnd' in flow.turn.ws.sent[-1]['realtimeInput'])
        self.assertNotIn('audioStreamEnd',json.dumps(flow.turn.ws.sent))
        await self.stop(task)
        flow=self.flow();flow.active=True;flow.epoch=1;flow.input_bytes=1920000
        flow.bridge.audio.put_nowait({'type':'audio','epoch':1,'data':base64.b64encode(bytes(640)).decode()})
        with self.assertRaisesRegex(LiveError,'INPUT_BYTE_LIMIT'):await flow.send_input()

    async def test_interruption_drops_old_chunks_until_terminal_barrier(self):
        flow=self.flow();flow.pending=1;flow.epoch=1;flow.barrier.clear()
        send=asyncio.create_task(flow.send_input())
        flow.bridge.audio.put_nowait({'type':'speech_start','epoch':2})
        flow.bridge.audio.put_nowait({'type':'audio','epoch':2,'data':base64.b64encode(bytes(640)).decode()})
        flow.bridge.audio.put_nowait({'type':'speech_end','epoch':2})
        await self.spin(lambda:flow.active and flow.suppressed)
        await asyncio.sleep(0)
        self.assertFalse(any('activityEnd' in m['realtimeInput'] for m in flow.turn.ws.sent))
        events=asyncio.Queue()
        async def receive(t):return await events.get()
        flow.turn.receive=receive
        reader=asyncio.create_task(flow.receive_output())
        await events.put(audio_event(bytes(960),complete=False))
        await events.put({'serverContent':{'interrupted':True}})
        await events.put({'serverContent':{'turnComplete':True}})
        await self.spin(lambda:flow.pending==2)
        output_calls=[c.args[0] for c in flow.bridge.write.call_args_list]
        self.assertEqual(output_calls,[{'type':'cancel_output','epoch':1}])
        await events.put(audio_event(bytes(1920)))
        await self.spin(lambda:flow.pending is None)
        calls=[c.args[0] for c in flow.bridge.write.call_args_list]
        self.assertEqual([m['type'] for m in calls],['cancel_output','output','output_end'])
        self.assertTrue(all(m['epoch']==2 for m in calls[1:]))
        await self.stop(send);await self.stop(reader)

    async def test_fast_second_interruption_preserves_epoch_and_drops_audio(self):
        flow=self.flow();flow.pending=2;flow.epoch=2;flow.barrier.clear()
        send=asyncio.create_task(flow.send_input())
        flow.bridge.audio.put_nowait({'type':'speech_start','epoch':3})
        await self.spin(lambda:flow.suppressed)
        self.assertEqual(flow.epoch,3)
        flow.turn.receive=AsyncMock(side_effect=[audio_event(bytes(960),False),{'serverContent':{'interrupted':True,'turnComplete':True}},LiveError('STOP')])
        with self.assertRaisesRegex(LiveError,'STOP'):await flow.receive_output()
        self.assertEqual(flow.bridge.write.call_args_list[0].args[0],{'type':'cancel_output','epoch':2})
        flow.bridge.write.assert_awaited_once()
        await self.stop(send)

    async def test_ambiguous_unsolicited_or_after_generation_audio_stops(self):
        for pending,ended,event,code in [(None,False,audio_event(bytes(960)),'GOOGLE_UNSOLICITED_OUTPUT'),
            (1,True,audio_event(bytes(960)),'GOOGLE_AUDIO_AFTER_GENERATION'),
            (1,False,{'serverContent':{'interrupted':True}},'GOOGLE_UNEXPECTED_INTERRUPTION')]:
            flow=self.flow();flow.pending=pending;flow.pending_ended=ended;flow.turn.ws=Socket([event])
            with self.assertRaisesRegex(LiveError,code):await flow.receive_output()
        flow=self.flow();flow.pending=1;flow.turn.ws=Socket([{'goAway':{'timeLeft':'1s'}}])
        with self.assertRaisesRegex(LiveError,'GOOGLE_UNEXPECTED_ACTION'):await flow.receive_output()

    async def test_watch_suppressed_and_response_timeouts_not_idle_timer(self):
        for suppressed,code in [(True,'GOOGLE_INTERRUPTION_TIMEOUT'),(False,'GOOGLE_RESPONSE_IDLE_TIMEOUT')]:
            flow=self.flow();flow.deadline=0;flow.suppressed=suppressed
            with self.assertRaisesRegex(LiveError,code):await flow.watch()

    async def test_output_cap_and_input_epoch_errors_fail_closed(self):
        flow=self.flow();flow.pending=1;flow.turn.ws=Socket([audio_event(bytes(480000))])
        with self.assertRaisesRegex(LiveError,'OUTPUT_LIMIT'):await flow.receive_output()
        flow.bridge.write.assert_not_awaited()
        for event in ({'type':'speech_start','epoch':2},{'type':'speech_end','epoch':1},
                      {'type':'audio','epoch':1,'data':'AAAA'}):
            flow=self.flow();flow.bridge.audio.put_nowait(event)
            with self.assertRaisesRegex(LiveError,'INPUT_EPOCH_INVALID'):await flow.send_input()

    async def test_exchange_always_closes_and_no_credentials_before_free_gate(self):
        turn=types.SimpleNamespace(open=AsyncMock(),close=AsyncMock())
        bridge=types.SimpleNamespace(safe=lambda:True,future=lambda x:None,write=AsyncMock(),wait=AsyncMock())
        with patch.object(runner,'DuplexTurn',return_value=turn),patch.object(runner,'DuplexFlow') as cls:
            cls.return_value.run=AsyncMock(side_effect=LiveError('SAFETY_STOP'))
            with self.assertRaisesRegex(LiveError,'SAFETY_STOP'):await runner.exchange(bridge,'DUMMY')
        turn.close.assert_awaited_once()
        with patch.object(runner,'existing_token',side_effect=AssertionError('credentials')):
            with self.assertRaisesRegex(LiveError,'EXISTING_KEY_FREE_TIER_NOT_CONFIRMED'):await runner.run(42)


GATE=r'''
const {SpeechGate}=require(process.argv[1]+'/voice_live/speech_gate.cjs'),assert=require('node:assert/strict');
let now=0,events=[],reason=null;const gate=new SpeechGate(m=>events.push(m),r=>reason=r,()=>now);
function frame(level){const b=Buffer.alloc(640);for(let i=0;i<640;i+=2)b.writeInt16LE(Math.round(level*Math.sin(i/2*440*2*Math.PI/16000)),i);return b;}
for(let i=0;i<100;i++){now+=20;gate.accept(frame(0));gate.tick();}
assert.equal(events.length,0);assert.equal(gate.pre.length,10);
for(let i=0;i<2;i++){now+=20;gate.accept(frame(300));}assert.equal(events.length,0);
now+=20;gate.accept(frame(300));assert.equal(events[0].type,'speech_start');assert.equal(events.filter(x=>x.type==='audio').length,10);
assert.equal(Buffer.from(events[1].data,'base64').length,640);assert.equal(gate.active,true);
now+=801;gate.tick();assert.equal(events.at(-1).type,'speech_end');assert.equal(gate.active,false);
let original=gate.idle;gate.setPlaying(true);now+=59000;gate.tick();assert.equal(gate.idle,original);assert.equal(reason,null);
gate.setPlaying(false);now+=60000;gate.tick();assert.equal(reason,'IDLE_LIMIT');gate.destroy();assert.equal(gate.pre.length,0);
// Long real utterance survives8s but is stopped at60s; low energy noise never starts.
now=0;events=[];reason=null;const long=new SpeechGate(m=>events.push(m),r=>reason=r,()=>now);
for(let i=0;i<451;i++){now+=20;long.accept(frame(300));long.tick();}assert.equal(reason,null);assert.equal(long.active,true);
now=60100;long.tick();assert.equal(reason,'INPUT_LIMIT');long.destroy();
now=0;events=[];reason=null;const quiet=new SpeechGate(m=>events.push(m),r=>reason=r,()=>now);
for(let i=0;i<100;i++){now+=20;quiet.accept(frame(50));quiet.tick();}assert.equal(events.length,0);
for(let i=0;i<3;i++){now+=20;quiet.accept(frame(300));quiet.tick();}assert.equal(events[0].type,'speech_start');
quiet.destroy();console.log('GATE_OK');
'''
PREFIX=HARNESS[:HARNESS.index('(async()=>{')].replace("root+'/voice_live/worker.cjs'","root+'/voice_live/worker_duplex.cjs'")
PREFIX=PREFIX.replace('decrypt:p=>p','decrypt:(p,user)=>p')
PREFIX=PREFIX.replace("if(name==='./audio.cjs')return{Converter};", "if(name==='./audio.cjs')return{Converter};if(name==='./speech_gate.cjs')return require(root+'/voice_live/speech_gate.cjs');if(name==='./receive_report.cjs')return require(root+'/voice_live/receive_report.cjs');")
BODY=r'''
(async()=>{
 await new Promise(setImmediate);send({type:'listen'});
 const encoder=new O(48000,2,O.Application.AUDIO),pcm=Buffer.alloc(3840),mono=Buffer.alloc(24000);let seq=0;
 for(let i=0;i<pcm.length;i+=2)pcm.writeInt16LE(Math.round(3000*Math.sin(i/2*440*2*Math.PI/48000)),i);
 for(let i=0;i<mono.length;i+=2)mono.writeInt16LE(Math.round(3000*Math.sin(i/2*440*2*Math.PI/24000)),i);
 async function inputPacket(silent=false){
  const opus=encoder.encode(silent?Buffer.alloc(3840):pcm,960),header=Buffer.alloc(12),nonce=Buffer.alloc(12),suffix=Buffer.alloc(4);const n=seq++;
  header[0]=0x80;header[1]=0x78;header.writeUInt16BE(n,2);header.writeUInt32BE(n*960,4);header.writeUInt32BE(ssrc,8);nonce.writeUInt32BE(n,0);suffix.writeUInt32BE(n);
  const cipher=crypto.createCipheriv('aes-256-gcm',key,nonce);cipher.setAAD(header);
  conn.receiver.onUdpMessage(Buffer.concat([header,cipher.update(opus),cipher.final(),cipher.getAuthTag(),suffix]));await new Promise(setImmediate);
 }
 // Encoded digital silence must not upload or open a Google turn.
 for(let i=0;i<12;i++)await inputPacket(true);
 assert.equal(messages.filter(m=>m.type==='audio').length,0);assert.equal(messages.filter(m=>m.type==='speech_start').length,0);
 for(let i=0;i<8;i++)await inputPacket();
 assert.equal(messages.filter(m=>m.type==='speech_start').length,1);
 await new Promise(r=>setTimeout(r,850));timers.filter(t=>t.ms===25&&!t.cancelled).at(-1).fn();
 assert.equal(messages.filter(m=>m.type==='speech_end').length,1);
 const response=mode==='tiny'?Buffer.alloc(2):mode==='silent'?Buffer.alloc(24000):mono;
 send({type:'output',epoch:1,data:response.toString('base64')});
 if(['tiny','silent'].includes(mode)){send({type:'output_end',epoch:1});}
 else if(mode==='buffering'){
  for(let i=0;i<8;i++)await inputPacket();
 }else{
  await new Promise(r=>setTimeout(r,90));assert.ok(messages.some(m=>m.type==='playing'));
  if(mode==='barge')for(let i=0;i<8;i++)await inputPacket();
  else if(mode==='stop')send({type:'stop'});
  else if(mode==='dave'){encrypted=false;timers.filter(t=>t.ms===25&&!t.cancelled).at(-1).fn();}
  else if(mode==='member')conn.receiver.speaking.emit('start','500000000000000');
  else if(mode==='deadline')timers.find(t=>t.ms===600000).fn();
  else if(mode==='outputlimit')timers.filter(t=>t.ms===10000&&!t.cancelled).at(-1).fn();
 }
 if(['barge','buffering'].includes(mode)){
  assert.equal(messages.filter(m=>m.type==='speech_start').length,2);
  // Late old epoch chunks and its cancellation cannot destroy new input/output.
  send({type:'output',epoch:1,data:mono.toString('base64')});send({type:'output_end',epoch:1});send({type:'cancel_output',epoch:1});
  await new Promise(r=>setTimeout(r,850));timers.filter(t=>t.ms===25&&!t.cancelled).at(-1).fn();
  send({type:'output',epoch:2,data:mono.subarray(0,9600).toString('base64')});send({type:'output_end',epoch:2});
  await new Promise(r=>setTimeout(r,450));assert.ok(messages.some(m=>m.type==='reply_done'&&m.epoch===2));send({type:'stop'});
 }
 await new Promise(setImmediate);const result=messages.find(m=>m.type==='result');assert.ok(result);assert.equal(destroyed,true);
 assert.deepEqual(subscriptions,[target]);console.log(JSON.stringify({result,types:messages.map(m=>m.type),dispatched,nonzero}));
 encoder.delete();outputDecoder.delete();key.fill(0);pcm.fill(0);mono.fill(0);process.exit(0);
})().catch(e=>{console.error(e.stack);console.error(JSON.stringify(messages.map(m=>({type:m.type,epoch:m.epoch,code:m.code}))));process.exit(1);});
'''


class RealDuplex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('node') or not (ROOT/'voice_probe/node_modules/opusscript').exists():raise unittest.SkipTest('existing dependencies needed')
    def test_local_gate_timers_silence_onset_preroll_caps(self):
        p=subprocess.run(['node','-e',GATE,str(ROOT)],capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout.strip(),'GATE_OK')
    def test_real_simultaneous_receive_playback_interrupt_and_safety(self):
        for mode,code in [('barge','STOPPED'),('buffering','STOPPED'),('stop','STOPPED'),('dave','DAVE_NOT_READY'),
                          ('member','OTHER_SPEAKER'),('deadline','SESSION_LIMIT'),('outputlimit','OUTPUT_LIMIT'),
                          ('tiny','GOOGLE_AUDIO_TOO_SHORT'),('silent','GOOGLE_AUDIO_SILENT')]:
            with self.subTest(mode=mode):
                p=subprocess.run(['node','-e',PREFIX+BODY,str(ROOT),mode],capture_output=True,text=True,timeout=6)
                self.assertEqual(p.returncode,0,p.stderr)
                data=json.loads(p.stdout);self.assertEqual(data['result']['code'],code)
                if mode in ('barge','buffering'):
                    self.assertEqual(data['result']['turnsCompleted'],1)
                    self.assertEqual(data['result']['droppedChunks'],2)
                    self.assertGreater(data['nonzero'],0)


class DuplexRunnerGates(unittest.IsolatedAsyncioTestCase):
    async def test_stop_member_deadline_and_start_gate(self):
        import test_voice_live_session as fixture
        test = fixture.RunnerSessionGates()
        with patch.object(fixture, 'runner', runner):
            await test.exercise()
            await test.exercise(safety=True)
            await test.exercise(answer='CANCEL')
            await test.exercise(foreign=True)

    async def test_concurrent_flow_stop_cancels_reader_and_writer(self):
        turn=DuplexTurn('DUMMY',lambda:True)
        ws=types.SimpleNamespace(send_json=AsyncMock())
        turn.ws=ws
        began=asyncio.Event()
        async def receive(timeout):began.set();await asyncio.Event().wait()
        turn.receive=receive
        bridge=types.SimpleNamespace(audio=asyncio.Queue(),write=AsyncMock())
        flow=DuplexFlow(turn,bridge)
        task=asyncio.create_task(flow.run());await began.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,.5)

    async def test_bounded_write_cancellation_not_lost_on_fast_completion(self):
        from voice_live.duplex_protocol import bounded
        for _ in range(20):
            task=asyncio.create_task(bounded(AsyncMock()(),1))
            await asyncio.sleep(0);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,.5)
