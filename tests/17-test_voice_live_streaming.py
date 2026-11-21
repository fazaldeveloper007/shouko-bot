"""Actual pinned receiver, Opus codec and Discord audio player, no network."""
import json
import pathlib
import shutil
import subprocess
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
HARNESS=r'''
const root=process.argv[1],mode=process.argv[2];
const fs=require('node:fs'),vm=require('node:vm'),E=require('node:events'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const v=require(root+'/voice_probe/node_modules/@discordjs/voice');
const O=require(root+'/voice_probe/node_modules/opusscript');
const {Converter}=require(root+'/voice_live/audio.cjs');
const input=new E(),conn=new E(),messages=[],timers=[];
let encrypted=true,destroyed=false,dispatched=0,nonzero=0,subscriptions=[];
conn.state={status:'ready',networking:{state:{code:4,dave:{decrypt:p=>p}}}};
conn.receiver=new v.VoiceReceiver(conn);
const originalSubscribe=conn.receiver.subscribe.bind(conn.receiver);
conn.receiver.subscribe=(id,opts)=>{subscriptions.push(id);return originalSubscribe(id,opts);};
conn.destroy=()=>{destroyed=true;};conn.setSpeaking=()=>{};conn.onSubscriptionRemoved=()=>{};
const outputDecoder=new O(48000,2,O.Application.AUDIO);
conn.prepareAudioPacket=packet=>{
 const decoded=outputDecoder.decode(packet);dispatched++;
 for(let i=0;i<decoded.length;i+=2)if(decoded.readInt16LE(i))nonzero++;
 decoded.fill(0);
};conn.dispatchAudio=()=>{};
conn.subscribe=player=>player.subscribe(conn);
const target='400000000000000',ssrc=12345,key=crypto.randomBytes(32);
conn.receiver.ssrcMap.update({userId:target,audioSSRC:ssrc});
conn.receiver.connectionData={encryptionMode:'aead_aes256_gcm_rtpsize',nonceBuffer:Buffer.alloc(12),secretKey:key};
const sandbox={Buffer,AbortController,require:name=>{
 if(name==='node:readline')return{createInterface:()=>input};
 if(name==='node:stream')return require(name);
 if(name==='../voice_probe/node_modules/@discordjs/voice')return{...v,joinVoiceChannel:opts=>{assert.equal(opts.selfMute,false);assert.equal(opts.selfDeaf,false);assert.equal(opts.daveEncryption,true);return conn;}};
 if(name==='../voice_probe/node_modules/opusscript')return O;
 if(name==='../music/dave_gate.cjs')return{encrypted:()=>encrypted,proof:()=>({transportReady:true,sessionReady:encrypted}),waitEncrypted:async()=>{}};
 if(name==='./audio.cjs')return{Converter};
 throw Error('UNEXPECTED_MODULE');
},setTimeout:(fn,ms)=>{const t={fn,ms,unref(){}};timers.push(t);return t;},clearTimeout:t=>{if(t)t.cancelled=true;},setImmediate:fn=>setImmediate(fn),process:{stdout:{write:s=>{messages.push(JSON.parse(s));return true;}},stdin:{},on:()=>{},exit:()=>{},exitCode:0}};
vm.runInNewContext(fs.readFileSync(root+'/voice_live/worker.cjs','utf8'),sandbox);
const send=m=>input.emit('line',JSON.stringify(m));
send({type:'init',guild:'100000000000000',channel:'200000000000000',user:'300000000000000',target});
conn.emit('stateChange',{}, {status:'ready'});
(async()=>{
 await new Promise(setImmediate);send({type:'listen'});
 const encoder=new O(48000,2,O.Application.AUDIO),pcm=Buffer.alloc(3840);
 for(let i=0;i<pcm.length;i+=2)pcm.writeInt16LE(Math.round(Math.sin(i/2*440*2*Math.PI/48000)*3000),i);
 // Real encrypted RTP transport and real Opus input through actual receive stream.
 for(let n=0;n<10;n++){
  const opus=encoder.encode(pcm,960),header=Buffer.alloc(12),nonce=Buffer.alloc(12),suffix=Buffer.alloc(4);
  header[0]=0x80;header[1]=0x78;header.writeUInt16BE(n,2);header.writeUInt32BE(n*960,4);header.writeUInt32BE(ssrc,8);nonce.writeUInt32BE(n,0);suffix.writeUInt32BE(n);
  const cipher=crypto.createCipheriv('aes-256-gcm',key,nonce);cipher.setAAD(header);
  const enc=Buffer.concat([cipher.update(opus),cipher.final()]);
  conn.receiver.onUdpMessage(Buffer.concat([header,enc,cipher.getAuthTag(),suffix]));
  await new Promise(setImmediate);
 }
 timers.find(t=>t.ms===8000).fn();
 const mono=Buffer.alloc(9600);
 for(let i=0;i<mono.length;i+=2)mono.writeInt16LE(Math.round(Math.sin(i/2*440*2*Math.PI/24000)*3000),i);
 if(mode==='dave_loss')encrypted=false;
 const response=mode==='tiny'?Buffer.alloc(2):mode==='silent'?Buffer.alloc(9600):mono;
 send({type:'output',data:response.toString('base64')});
 if(mode==='other_speaker')conn.receiver.speaking.emit('start','500000000000000');
 if(mode==='stop')send({type:'stop'});
 if(mode==='overall')timers.find(t=>t.ms===30000).fn();
 if(mode==='output_timeout'){
  await new Promise(resolve=>setTimeout(resolve,90));
  const timer=timers.filter(t=>t.ms===10000&&!t.cancelled).at(-1);assert.ok(timer);timer.fn();
 }else if(!['dave_loss','other_speaker','stop','overall'].includes(mode))send({type:'output_end'});
 await new Promise(resolve=>setTimeout(resolve,450));
 const result=messages.find(m=>m.type==='result');
 assert.ok(result,'No final result from real AudioPlayer');
 assert.deepEqual(subscriptions,[target]);
 const audio=messages.filter(m=>m.type==='audio');
 assert.equal(audio.length,10);assert.equal(audio.reduce((sum,m)=>sum+Buffer.from(m.data,'base64').length,0),6400);
 assert.ok(messages.some(m=>m.type==='input_end'));
 assert.equal(result.inputBytes,6400);assert.equal(destroyed,true);
 console.log(JSON.stringify({result,dispatched,nonzero,types:messages.map(m=>m.type)}));
 pcm.fill(0);mono.fill(0);encoder.delete();outputDecoder.delete();key.fill(0);process.exit(0);
})().catch(error=>{console.error(error.stack);console.error(JSON.stringify(messages.map(m=>({type:m.type,code:m.code}))));process.exit(1);});
'''
FIR=r'''
const assert=require('node:assert/strict'),root=process.argv[1];
const {Converter}=require(root+'/voice_live/audio.cjs');
function tone(hz){const b=Buffer.alloc(48000*4);for(let n=0;n<48000;n++){const value=Math.round(8000*Math.sin(n*hz*2*Math.PI/48000));b.writeInt16LE(value,n*4);b.writeInt16LE(value,n*4+2);}return b;}
function rms(buffer){let sum=0,n=0;for(let i=2000;i<buffer.length;i+=2){sum+=buffer.readInt16LE(i)**2;n++;}return Math.sqrt(sum/n);}
const full=new Converter('input'),chunked=new Converter('input'),low=tone(1000),high=tone(12000);
const once=full.convert(low),pieces=[];
for(let i=0;i<low.length;i+=3840)pieces.push(chunked.convert(low.subarray(i,i+3840)));
assert.deepEqual(once,Buffer.concat(pieces));assert.equal(once.length,32000);
const anti=new Converter('input');assert.ok(rms(anti.convert(high))<rms(once)*0.02);
const up=new Converter('output'),stereo=up.convert(once);assert.equal(stereo.length,128000);
for(let i=0;i<stereo.length;i+=4)assert.equal(stereo.readInt16LE(i),stereo.readInt16LE(i+2));
full.destroy();chunked.destroy();anti.destroy();up.destroy();assert.ok(full.history.every(x=>x===0));
console.log('FIR_OK');
'''


class RealStreaming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('node') or not (ROOT/'voice_probe/node_modules/opusscript').exists():
            raise unittest.SkipTest('Install existing voice_probe dependencies')

    def scenario(self, name):
        result=subprocess.run(['node','-e',HARNESS,str(ROOT),name],text=True,capture_output=True,timeout=5,check=True)
        return json.loads(result.stdout)

    def test_real_input_player_encode_and_output_decode(self):
        data=self.scenario('complete')
        self.assertEqual(data['result']['code'],'COMPLETE')
        self.assertEqual(data['result']['outputBytes'],9600)
        self.assertGreater(data['dispatched'],0)
        self.assertGreater(data['nonzero'],0)

    def test_stop_safety_dave_and_deadlines_destroy_connection(self):
        for mode,code in [('stop','STOPPED'),('other_speaker','OTHER_SPEAKER'),
                          ('dave_loss','DAVE_NOT_READY'),('overall','OVERALL_LIMIT'),
                          ('output_timeout','OUTPUT_LIMIT')]:
            with self.subTest(mode=mode):
                self.assertEqual(self.scenario(mode)['result']['code'],code)

    def test_tiny_and_digital_silence_never_report_spoken_success(self):
        for mode,code in [('tiny','GOOGLE_AUDIO_TOO_SHORT'),('silent','GOOGLE_AUDIO_SILENT')]:
            with self.subTest(mode=mode):
                data=self.scenario(mode)
                self.assertEqual(data['result']['code'],code)
                self.assertEqual(data['result']['outputNonzeroSamples'],0)
                if mode=='tiny':
                    self.assertEqual(data['result']['outputFramesRead'],0)
                    self.assertNotIn('playing',data['types'])

    def test_streaming_resampling_alignment_and_antialiasing(self):
        data=subprocess.run(['node','-e',FIR,str(ROOT)],text=True,capture_output=True,timeout=5,check=True)
        self.assertEqual(data.stdout.strip(),'FIR_OK')
