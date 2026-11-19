'use strict';
// Local-only probe. No audio files, playback, transcript, token, or AI request.
const readline = require('node:readline');
const v = require('@discordjs/voice');
const OpusScript = require('opusscript');
const receiveDiagnostics = require('./receive_diagnostics.cjs');
const {proof, encrypted, waitEncrypted} = require('../music/dave_gate.cjs');
const out = m => process.stdout.write(JSON.stringify(m)+'\n');
let cfg, conn, adapter, stream, decoder, ending=false, ready=false, serial=0;
let deadline, connectDeadline, initDeadline, encryptionWaiting=false, diagnostics;
let receiveErrorClass;
const acks=new Map();
const stats={packets:0, samples:0, nonzeroSamples:0, peak:0};
function finish(code='STOPPED') {
 if(ending)return; ending=true;
 // Fixed proof projection only, before connection.destroy discards the state.
 // Never expose raw networking objects, keys, session IDs or privacy codes.
 const daveProof=conn?proof(conn):null;
 clearTimeout(deadline); clearTimeout(connectDeadline);
 for(const t of acks.values())clearTimeout(t); acks.clear();
 stream?.destroy(); stream=null;
 diagnostics?.restore(); diagnostics=null;
 try{decoder?.delete();}catch{} decoder=null;
 try{conn?.destroy();}catch{}
 out({type:'result',code,...stats,daveProof,...(receiveErrorClass?{errorClass:receiveErrorClass}:{})});
 // Decoder memory is released; JS/native allocators cannot promise forensic zeroization.
 process.exitCode=code==='STOPPED'||code==='TIME_LIMIT'?0:1;
 setImmediate(()=>process.exit(process.exitCode));
}
function sendPayload(payload) {
 if(ending || payload.op!==4 || payload.d?.guild_id!==cfg.guild || payload.d?.channel_id!==cfg.channel)return false;
 const id=++serial; acks.set(id,setTimeout(()=>finish('GATEWAY_ACK_TIMEOUT'),5000));
 out({type:'gateway_send',id,payload});return true;
}
function startReceive() {
 if(ending||ready)return; ready=true;
 decoder=new OpusScript(48000,2,OpusScript.Application.AUDIO);
 // Subscription happens for one verified requester only. Other SSRCs have no stream.
 conn.receiver.speaking.on('start', id=>{if(id!==cfg.target)finish('OTHER_SPEAKER');});
 diagnostics=receiveDiagnostics.install(conn.receiver);
 stream=conn.receiver.subscribe(cfg.target,{end:{behavior:v.EndBehaviorType.Manual}});
 stream.on('error',error=>{
  const failure=diagnostics?.classify(error)||{code:'RECEIVE_UNCLASSIFIED_ERROR',errorClass:receiveDiagnostics.errorClass(error)};
  receiveErrorClass=failure.errorClass;finish(failure.code);
 });
 stream.on('data', packet=>{
  if(ending)return;
  if(!encrypted(conn))return finish('DAVE_NOT_READY');
  let pcm;
  try{
   pcm=decoder.decode(packet);
   if(!Buffer.isBuffer(pcm)||pcm.length===0||pcm.length>23040||pcm.length%4)throw Error();
   stats.packets++;
   for(let i=0;i<pcm.length;i+=2){const n=Math.abs(pcm.readInt16LE(i));stats.samples++;if(n)stats.nonzeroSamples++;stats.peak=Math.max(stats.peak,n);}
  }catch{finish('DECODE_ERROR');}
  finally{pcm?.fill(0);packet.fill(0);}
 });
 out({type:'voice_ready'});
 deadline=setTimeout(()=>finish('TIME_LIMIT'),20000);
}
function message(m) {
 if(m.type==='init') {
  if(cfg||![m.guild,m.channel,m.user,m.target].every(x=>typeof x==='string'&&/^\d{15,22}$/.test(x)))throw Error();
  cfg=m; clearTimeout(initDeadline); connectDeadline=setTimeout(()=>finish('CONNECT_TIMEOUT'),25000);
  conn=v.joinVoiceChannel({guildId:m.guild,channelId:m.channel,selfDeaf:false,selfMute:true,daveEncryption:true,debug:false,group:'kristy-receive-probe',adapterCreator:api=>{adapter=api;return{sendPayload,destroy:()=>{}};}});
  conn.on('error',()=>finish('VOICE_ERROR'));
  conn.on('stateChange',(_,s)=>{
   if(ending)return;
   if(s.status===v.VoiceConnectionStatus.Ready&&!ready&&!encryptionWaiting){
    encryptionWaiting=true;
    waitEncrypted(conn,{timeoutMs:10000}).then(()=>{clearTimeout(connectDeadline);startReceive();}).catch(error=>{
     const code=error?.message==='DAVE_WAIT_TIMEOUT'?'DAVE_TIMEOUT':
                error?.message==='DAVE_CONNECTION_LOST'?'DAVE_CONNECTION_LOST':
                error?.message==='DAVE_WAIT_CANCELLED'?'DAVE_CANCELLED':'DAVE_WAIT_FAILED';
     finish(code);
    });
   }else if(ready&&s.status!==v.VoiceConnectionStatus.Ready)finish('VOICE_DISCONNECTED');
  });
 }else if(m.type==='ack') {
  const t=acks.get(m.id);if(!t)return;clearTimeout(t);acks.delete(m.id);if(!m.ok)finish('GATEWAY_SEND_FAILED');
 }else if(m.type==='event') {
  if(!cfg||ending||m.data?.guild_id!==cfg.guild)return;
  if(m.kind==='state'&&m.data.user_id===cfg.user){if(m.data.channel_id!==cfg.channel)return finish('VOICE_MOVED');adapter.onVoiceStateUpdate(m.data);}
  else if(m.kind==='server')adapter.onVoiceServerUpdate(m.data);
 }else if(m.type==='stop')finish();
 else throw Error();
}
const input=readline.createInterface({input:process.stdin});
input.on('line',line=>{try{if(line.length>65536)throw Error();message(JSON.parse(line));}catch{finish('INVALID_COMMAND');}});
input.on('close',()=>finish());
process.on('SIGTERM',()=>finish());
process.on('uncaughtException',()=>finish('PROBE_ERROR'));
initDeadline=setTimeout(()=>finish('INIT_TIMEOUT'),30000);initDeadline.unref();
