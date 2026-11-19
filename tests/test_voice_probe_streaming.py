"""Actual pinned VoiceReceiver/AudioReceiveStream + AES-GCM RTP + real Opus.
DAVE session is synthetic: these are not a Discord/DAVE cryptography proof.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
'use strict';
const path=require('node:path'),fs=require('node:fs'),vm=require('node:vm'),E=require('node:events'),crypto=require('node:crypto');
const root=process.argv[1],mode=process.argv[2]||'good';
const v=require(path.join(root,'voice_probe/node_modules/@discordjs/voice'));
const O=require(path.join(root,'voice_probe/node_modules/opusscript'));
const input=new E(),conn=new E(),messages=[];
conn.state={status:'ready',networking:{state:{code:4,dave:{decrypt:p=>{if(mode==='dave'&&messages.filter(x=>x.type==='voice_ready').length&&davePackets++===5)throw new TypeError('SYNTHETIC_PRIVATE_ERROR');return p;}}}}};
conn.receiver=new v.VoiceReceiver(conn);conn.destroy=()=>{};
const target='400000000000000',ssrc=12345;
conn.receiver.ssrcMap.update({userId:target,audioSSRC:ssrc});
const key=crypto.randomBytes(32);
conn.receiver.connectionData={encryptionMode:'aead_aes256_gcm_rtpsize',nonceBuffer:Buffer.alloc(12),secretKey:key};
let timer,davePackets=0;
const sandbox={Buffer,require:name=>{
 if(name==='node:readline')return{createInterface:()=>input};
 if(name==='@discordjs/voice')return{...v,joinVoiceChannel:()=>conn};
 if(name==='opusscript')return O;
 if(name==='./receive_diagnostics.cjs')return require(path.join(root,'voice_probe/receive_diagnostics.cjs'));
 if(name==='../music/dave_gate.cjs')return{proof:()=>({transportReady:true,protocol:1,sessionReady:true,privacyPresent:true,reinitializing:false,transitionExecuted:true,pendingTransitions:0}),encrypted:()=>true,waitEncrypted:async()=>{}};
 throw Error('Unexpected dependency');
},setTimeout:(fn,ms)=>{if(ms===20000)timer=fn;return{unref(){}};},clearTimeout:()=>{},setImmediate:fn=>fn(),process:{stdout:{write:s=>messages.push(JSON.parse(s))},stdin:{},on:()=>{},exit:()=>{},exitCode:0}};
vm.runInNewContext(fs.readFileSync(path.join(root,'voice_probe/worker.cjs'),'utf8'),sandbox);
input.emit('line',JSON.stringify({type:'init',guild:'100000000000000',channel:'200000000000000',user:'300000000000000',target}));
conn.emit('stateChange',{}, {status:'ready'});
(async()=>{
 await new Promise(setImmediate);
 const encoder=new O(48000,2,O.Application.AUDIO),pcm=Buffer.alloc(3840);
 for(let i=0;i<pcm.length;i+=2)pcm.writeInt16LE(Math.round(Math.sin(i/2*440*2*Math.PI/48000)*3000),i);
 for(let n=0;n<100;n++){
  const opus=encoder.encode(pcm,960),header=Buffer.alloc(12),nonce=Buffer.alloc(12),suffix=Buffer.alloc(4);
  header[0]=0x80;header[1]=0x78;header.writeUInt16BE(n,2);header.writeUInt32BE(n*960,4);header.writeUInt32BE(ssrc,8);
  nonce.writeUInt32BE(n,0);suffix.writeUInt32BE(n);
  const cipher=crypto.createCipheriv('aes-256-gcm',key,nonce);cipher.setAAD(header);
  const encrypted=Buffer.concat([cipher.update(opus),cipher.final()]);
  const tag=cipher.getAuthTag();if(mode==='bad-auth'&&n===5)tag[0]^=1;
  const packet=Buffer.concat([header,encrypted,tag,suffix]);
  conn.receiver.onUdpMessage(packet);
  await new Promise(setImmediate);
  if(messages.some(x=>x.type==='result'))break;
 }
 input.emit('line',JSON.stringify({type:'stop'}));
 pcm.fill(0);encoder.delete();key.fill(0);
 console.log(JSON.stringify(messages));
 process.exit(0);
})();

'''
CONTRACT=r'''
const assert=require('node:assert/strict');
const root=process.argv[1];
const v=require(root+'/voice_probe/node_modules/@discordjs/voice');
const d=require(root+'/voice_probe/receive_diagnostics.cjs');
assert.equal(v.version,'0.19.2');
assert.equal(v.VoiceReceiver.prototype.decrypt.length,4);
assert.equal(v.VoiceReceiver.prototype.parsePacket.length,5);
const sentinel=new TypeError('SYNTHETIC_PRIVATE_ERROR');
const conn={state:{status:'ready',networking:{state:{code:4,dave:{decrypt(){throw sentinel;}}}}}};
const receiver=new v.VoiceReceiver(conn),decrypt=receiver.decrypt,parse=receiver.parsePacket;
const diagnostic=d.install(receiver);
assert.ok(diagnostic);assert.equal(d.install(receiver),diagnostic);
assert.notEqual(receiver.decrypt,decrypt);assert.notEqual(receiver.parsePacket,parse);
const crypto=require('node:crypto'),key=Buffer.alloc(32),nonce=Buffer.alloc(12),header=Buffer.alloc(12);
header[0]=0x80;header[1]=0x78;
const cipher=crypto.createCipheriv('aes-256-gcm',key,nonce);cipher.setAAD(header);
const body=Buffer.concat([cipher.update(Buffer.from([0xf8,0xff,0xfe])),cipher.final()]);
const packet=Buffer.concat([header,body,cipher.getAuthTag(),Buffer.alloc(4)]);
// Exact original exception identity survives wrapper and real parsePacket.
assert.throws(()=>receiver.parsePacket(packet,'aead_aes256_gcm_rtpsize',nonce,key,'400000000000000'),e=>e===sentinel);
assert.deepEqual(diagnostic.classify(sentinel),{code:'RECEIVE_PACKET_PARSE_OR_DAVE_ERROR',errorClass:'TYPE_ERROR'});
diagnostic.restore();diagnostic.restore();
assert.equal(receiver.decrypt,decrypt);assert.equal(receiver.parsePacket,parse);
assert.equal(Object.hasOwn(receiver,'decrypt'),false);assert.equal(Object.hasOwn(receiver,'parsePacket'),false);
const again=d.install(receiver);assert.notEqual(again,diagnostic);again.restore();
assert.equal(d.install({decrypt,parsePacket:parse}),null);
for(const name of ['SYNTHETIC_PRIVATE_ERROR','__proto__','toString',null])assert.equal(d.errorClass({name}),'OTHER');
assert.equal(d.errorClass({get name(){throw sentinel;}}),'OTHER');
// A later owner is never overwritten during teardown.
const third=d.install(receiver),replacement=()=>{};receiver.decrypt=replacement;third.restore();assert.equal(receiver.decrypt,replacement);
console.log('CONTRACT_OK');
'''

class RealReceiveStreaming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('node') or not (ROOT/'voice_probe/node_modules/opusscript').exists():
            raise unittest.SkipTest('Run npm ci --ignore-scripts in voice_probe first')

    def run_scenario(self, mode):
        result=subprocess.run(['node','-e',HARNESS,str(ROOT),mode],capture_output=True,text=True,timeout=10,check=True)
        self.assertNotIn('SYNTHETIC_PRIVATE_ERROR',result.stdout+result.stderr)
        messages=json.loads(result.stdout)
        self.assertEqual(sum(m['type']=='result' for m in messages),1)
        return messages[-1]

    def test_hundred_real_receiver_packets_with_real_opus_and_buffer_wiping(self):
        result=self.run_scenario('good')
        self.assertEqual(result['code'],'STOPPED')
        self.assertEqual(result['packets'],100)
        self.assertEqual(result['samples'],192000)
        self.assertGreater(result['nonzeroSamples'],0)
        self.assertNotIn('errorClass',result)

    def test_corrupt_transport_tag_stops_without_dropping_or_retrying(self):
        result=self.run_scenario('bad-auth')
        self.assertEqual(result['code'],'RECEIVE_TRANSPORT_ERROR')
        self.assertEqual(result['packets'],5)
        self.assertEqual(result['samples'],9600)
        self.assertEqual(result['errorClass'],'ERROR')

    def test_real_parse_path_synthetic_dave_error_is_distinct(self):
        result=self.run_scenario('dave')
        self.assertEqual(result['code'],'RECEIVE_PACKET_PARSE_OR_DAVE_ERROR')
        self.assertEqual(result['packets'],5)
        self.assertEqual(result['errorClass'],'TYPE_ERROR')

    def test_pinned_method_contract_exception_identity_and_idempotent_restore(self):
        result=subprocess.run(['node','-e',CONTRACT,str(ROOT)],capture_output=True,text=True,timeout=10,check=True)
        self.assertEqual(result.stdout.strip(),'CONTRACT_OK')

    def test_python_result_rejects_unlisted_error_class(self):
        spec=importlib.util.spec_from_file_location('probe_result_projection',ROOT/'voice_probe/receive.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertNotIn('errorClass',module.public_result({'errorClass':'SYNTHETIC_PRIVATE_ERROR'}))
        self.assertEqual(module.public_result({'errorClass':'RANGE_ERROR'})['errorClass'],'RANGE_ERROR')
