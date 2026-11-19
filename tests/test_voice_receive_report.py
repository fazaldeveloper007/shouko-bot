"""Offline reporting regressions using the actual pinned Discord receiver, no sockets."""
import json
import pathlib
import shutil
import subprocess
import unittest
from test_voice_live_duplex import PREFIX, BODY
from voice_live.duplex import public_result
ROOT=pathlib.Path(__file__).resolve().parents[1]

CLASSIFIER=r'''
const root=process.argv[1],mode=process.argv[2],assert=require('node:assert/strict'),crypto=require('node:crypto');
const v=require(root+'/voice_probe/node_modules/@discordjs/voice'),{install,errorClass}=require(root+'/voice_live/receive_report.cjs');
const sentinel=new TypeError('PRIVATE_ERROR_PAYLOAD');sentinel.code='PRIVATE_CODE';
let calls=0;const dave={decrypt(packet,user){calls++;if(mode==='dave')throw sentinel;return packet;}};
const conn={state:{status:'ready',networking:{state:{code:4,dave}}}},r=new v.VoiceReceiver(conn);
const original=r.parsePacket,dec=r.decrypt,dd=dave.decrypt;
if(mode==='contract'){r.parsePacket=function(){};assert.equal(install(r),null);console.log('OK');process.exit();}
if(mode==='frozen'){Object.preventExtensions(r);assert.equal(install(r),null);assert.equal(r.decrypt,dec);assert.equal(r.parsePacket,original);console.log('OK');process.exit();}
const diag=install(r);assert.ok(diag);
const key=Buffer.alloc(32,7),header=Buffer.alloc(12);header[0]=128;header[1]=120;
const c=crypto.createCipheriv('aes-256-gcm',key,Buffer.alloc(12));c.setAAD(header);
let packet=Buffer.concat([header,c.update(Buffer.from([1,2,3])),c.final(),c.getAuthTag(),Buffer.alloc(4)]);
if(mode==='transport')packet[13]^=1;
if(mode==='parse'){packet=Buffer.from([128]);}
let error;try{r.parsePacket(packet,mode==='parse'?'unsupported':'aead_aes256_gcm_rtpsize',Buffer.alloc(12),key,'123');}catch(e){error=e;}
if(mode==='success'){assert.equal(error,undefined);assert.equal(calls,1);}
else {
 assert.ok(error);
 const expected=mode==='dave'?'DAVE_DECRYPT':'TRANSPORT_DECRYPT';
 assert.equal(diag.classify(error).receiveStage,expected);
 if(mode==='dave')assert.equal(error,sentinel);
 assert.ok(!JSON.stringify(diag.classify(error)).includes('PRIVATE'));
 assert.deepEqual(diag.classify(new Error('PRIVATE')),{receiveStage:'STREAM_OTHER',errorClass:'ERROR'});
}
assert.equal(dave.decrypt,dd);
diag.restore();diag.restore();assert.equal(r.parsePacket,original);assert.equal(r.decrypt,dec);
for(const name of ['__proto__','constructor','PRIVATE',null])assert.equal(errorClass({name}),'OTHER');
assert.equal(errorClass({get name(){throw sentinel;}}),'OTHER');
console.log('OK');
'''

# Trigger a real subscription error only after12decoded silence packets.
INJECT=r'''
 if(mode==='receive_dave')conn.state.networking.state.dave.decrypt=function(packet,user){throw new TypeError('PRIVATE_DAVE_SENTINEL');};
 if(mode==='receive_stream')conn.receiver.subscriptions.get(target).destroy(new Error('PRIVATE_STREAM_SENTINEL'));
 else if(mode==='receive_transport'){
  // A malformed AEAD envelope fails before DAVE, through actual onUdpMessage.
  const bad=Buffer.alloc(32);bad[0]=128;bad[1]=120;bad.writeUInt32BE(ssrc,8);conn.receiver.onUdpMessage(bad);
 }else if(mode==='receive_parse'){
  // Authenticated RTP header advertises extension but is too short to read its length.
  const header=Buffer.alloc(14);
  header[0]=128;header[1]=120;header.writeUInt32BE(ssrc,8);header[12]=0xbe;header[13]=0xde;
  // Controlled transport return isolates original RTP-extension parsing.
  conn.receiver.decrypt=function(){return Buffer.alloc(1);};
  conn.receiver.onUdpMessage(header);
 }else await inputPacket(true);
 await new Promise(setImmediate);
 const result=messages.find(m=>m.type==='result');assert.ok(result);assert.equal(result.code,'RECEIVE_ERROR');
 assert.equal(result.packets,12);assert.equal(result.inputBytes,0);assert.equal(result.outputBytes,0);assert.equal(destroyed,true);
 assert.ok(!JSON.stringify(result).includes('PRIVATE'));
 encoder.delete();outputDecoder.delete();console.log(JSON.stringify(result));process.exit(0);
'''

class ReceiveReporting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('node') or not (ROOT/'voice_probe/node_modules/opusscript').exists():
            raise unittest.SkipTest('existing pinned dependencies needed')

    def test_classifier_original_exceptions_restoration_and_no_private_text(self):
        for mode in ('dave','transport','parse','success','contract','frozen'):
            with self.subTest(mode=mode):
                p=subprocess.run(['node','-e',CLASSIFIER,str(ROOT),mode],capture_output=True,text=True,timeout=5)
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertEqual(p.stdout.strip(),'OK')

    def test_worker_after_twelve_packets_fixed_stage_and_zero_upload(self):
        marker=" for(let i=0;i<8;i++)await inputPacket();"
        script=PREFIX+BODY[:BODY.index(marker)]+INJECT+"})().catch(e=>{console.error(e.stack);process.exit(1);});"
        for mode,stage in [('receive_dave','DAVE_DECRYPT'),('receive_transport','TRANSPORT_DECRYPT'),('receive_stream','STREAM_OTHER'),('receive_parse','RTP_PARSE')]:
            with self.subTest(mode=mode):
                p=subprocess.run(['node','-e',script,str(ROOT),mode],capture_output=True,text=True,timeout=5)
                self.assertEqual(p.returncode,0,p.stderr)
                result=json.loads(p.stdout)
                self.assertEqual(result['receiveStage'],stage)
                self.assertIn(result['errorClass'],{'ERROR','TYPE_ERROR','RANGE_ERROR','SYNTAX_ERROR','OTHER'})

    def test_public_result_allowlists_diagnostics(self):
        data=public_result({'code':'RECEIVE_ERROR','receiveStage':'DAVE_DECRYPT','errorClass':'TYPE_ERROR','message':'PRIVATE'})
        self.assertEqual(data['receiveStage'],'DAVE_DECRYPT')
        self.assertEqual(data['errorClass'],'TYPE_ERROR')
        self.assertNotIn('PRIVATE',json.dumps(data))
        for bad in ('PRIVATE','__proto__'):
            data=public_result({'receiveStage':bad,'errorClass':bad})
            self.assertNotIn('receiveStage',data)
            self.assertNotIn('errorClass',data)
