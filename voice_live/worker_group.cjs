'use strict';
// Separate manual local-gated full-duplex worker. No files or AI keys here.
const readline=require('node:readline');
const {Worker}=require('node:worker_threads');
const path=require('node:path');
const {Readable}=require('node:stream');
const v=require('../voice_probe/node_modules/@discordjs/voice');
const Opus=require('../voice_probe/node_modules/opusscript');
const {encrypted,waitEncrypted,proof}=require('../music/dave_gate.cjs');
const {Converter}=require('./audio.cjs');
const {GroupGate}=require('./group_gate.cjs');
const receiveReport=require('./receive_report.cjs');
const out=m=>process.stdout.write(JSON.stringify(m)+'\n');
let epoch=0,turnsCompleted=0,turnOutputBytes=0,turnNonzero=0,gate,droppedChunks=0;
let cfg,conn,adapter,stream,decoder,encoder,player,resource,inputFilter,outputFilter;
const sources=new Map();
let diagnostics,receiveFailure,draining=false,awaitingReply=false;
let ending=false,ready=false,listening=false,outputEnded=false,startedOutput=false;
let pending=Buffer.alloc(0),frames=[],audioStream,frameSent=0,issued=[],inputBytes=0,outputBytes=0,outputNonzeroSamples=0,packets=0;
let inputTimer,overallTimer,connectTimer,securityTimer,serial=0,encryptionWaiting=false;
const acks=new Map();
function finish(code='STOPPED'){
 if(ending)return;ending=true;listening=false;
 const daveProof=conn?proof(conn):null;gate?.destroy();
 for(const t of [inputTimer,overallTimer,connectTimer,securityTimer])clearTimeout(t);
 for(const t of acks.values())clearTimeout(t);acks.clear();
 diagnostics?.restore();diagnostics=null;
 for(const source of sources.values()){source.stream.destroy();source.decoder.terminate();}sources.clear();stream?.destroy();stream=null;player?.stop(true);audioStream?.destroy();
 for(const frame of frames.concat(issued))frame.fill(0);frames=[];issued=[];pending.fill(0);pending=Buffer.alloc(0);
 inputFilter?.destroy();outputFilter?.destroy();
 try{decoder?.delete();}catch{}try{encoder?.delete();}catch{}
 try{conn?.destroy();}catch{}
 out({type:'result',...(receiveFailure||{}),code,packets,inputBytes,outputBytes,outputNonzeroSamples,outputFramesRead:frameSent,turnsCompleted,droppedChunks,daveProof});
 process.exitCode=['COMPLETE','STOPPED'].includes(code)?0:1;
 setImmediate(()=>process.exit(process.exitCode));
}
function checkSecurity(){
 if(ending)return;
 if(ready&&!encrypted(conn))return finish('DAVE_NOT_READY');
 gate?.tick();
 securityTimer=setTimeout(checkSecurity,25);
}
function sendPayload(payload){
 if(ending||payload.op!==4||payload.d?.guild_id!==cfg.guild||payload.d?.channel_id!==cfg.channel)return false;
 const id=++serial;acks.set(id,setTimeout(()=>finish('GATEWAY_ACK_TIMEOUT'),3000));
 out({type:'gateway_send',id,payload});return true;
}
function cancelOutput(){
 gate?.setPlaying(false);
 const old=player;player=null;
 old?.removeAllListeners();old?.stop(true);audioStream?.destroy();audioStream=null;resource=null;
 for(const frame of frames.concat(issued))frame.fill(0);frames=[];issued=[];pending.fill(0);pending=Buffer.alloc(0);
 outputFilter?.destroy();outputFilter=null;try{encoder?.delete();}catch{}encoder=null;
 outputEnded=false;startedOutput=false;turnOutputBytes=turnNonzero=0;
}
function gateEvent(m){
 if(ending)return;
 if(m.type==='speech_start'){
  if(draining)return finish('SESSION_LIMIT');
  cancelOutput();epoch=m.epoch;
  out({type:'speech_start',epoch,speaker:m.speaker});
 }else if(m.type==='audio'){
  inputBytes+=Buffer.byteLength(m.data,'base64');
  if(inputBytes>19200000)return finish('SESSION_INPUT_LIMIT');
  if(!out(m))finish('IPC_BACKPRESSURE');
 }else if(m.type==='speech_end'){awaitingReply=true;out(m);}
}
function completeIfDone(){
 if(ending)return;
 if(outputEnded&&frames.length===0&&player?.state.status===v.AudioPlayerStatus.Idle){
  turnsCompleted++;awaitingReply=false;cancelOutput();out({type:'reply_done',epoch});
  if(draining)finish('SESSION_LIMIT');
 }
}
class OpusFrames extends Readable {
 constructor(){super({objectMode:true,highWaterMark:1});this.waiting=false;}
 _read(){
  if(ending)return;
  if(!encrypted(conn))return finish('DAVE_NOT_READY');
  const frame=frames.shift();
  if(frame){frameSent++;issued.push(frame);this.push(frame);}
  else if(outputEnded)this.push(null);
  else this.waiting=true;
 }
 wake(){if(this.waiting){this.waiting=false;this._read();}}
}
function ensurePlayer(){
 if(player)return;
 audioStream=new OpusFrames();audioStream.on('error',()=>finish('OUTPUT_STREAM_ERROR'));
 resource=v.createAudioResource(audioStream,{inputType:v.StreamType.Opus,silencePaddingFrames:0});
 player=v.createAudioPlayer({behaviors:{noSubscriber:v.NoSubscriberBehavior.Stop}});
 player.on('error',()=>finish('PLAYER_ERROR'));
 player.on('stateChange',(_,s)=>{
  if(s.status===v.AudioPlayerStatus.Playing&&!startedOutput){startedOutput=true;gate?.setPlaying(true);out({type:'playing',epoch});}
  if(s.status===v.AudioPlayerStatus.Idle){gate?.setPlaying(false);completeIfDone();}
 });
 conn.subscribe(player);player.play(resource);
}
function encodeOutput(pcm){
 const combined=Buffer.concat([pending,pcm]);pending.fill(0);pcm.fill(0);
 let offset=0;
 while(offset+3840<=combined.length){
  const frame=combined.subarray(offset,offset+3840);
  frames.push(encoder.encode(frame,960));offset+=3840;
 }
 pending=Buffer.from(combined.subarray(offset));combined.fill(0);
 if(frames.length>30000)return finish('OUTPUT_QUEUE_LIMIT');
 // Do not start a player on an incomplete frame.
 if(frames.length){ensurePlayer();audioStream.wake();}
}
function endOutput(){
 if(ending||outputEnded)return;
 // Less than one 20ms source frame is not a playable response. Never
 // pad a single sample into a claimed spoken reply.
 if(turnOutputBytes<960)return finish('GOOGLE_AUDIO_TOO_SHORT');
 if(!turnNonzero)return finish('GOOGLE_AUDIO_SILENT');
 if(pending.length){const frame=Buffer.alloc(3840);pending.copy(frame);pending.fill(0);pending=Buffer.alloc(0);frames.push(encoder.encode(frame,960));frame.fill(0);}
 outputEnded=true;
 if(!turnOutputBytes)return finish('NO_AUDIO_RESPONSE');
 ensurePlayer();audioStream.wake();completeIfDone();
}
function sessionDeadline(){
 if(ending||draining)return;
 if(!awaitingReply||gate?.active)return finish('SESSION_LIMIT');
 draining=true;out({type:'session_draining'});
}
function message(m){
 if(ending)return;
 if(m.type==='init'){
  if(cfg||![m.guild,m.channel,m.user].every(x=>typeof x==='string'&&/^\d{15,22}$/.test(x))||!Array.isArray(m.targets)||m.targets.length<1||m.targets.length>8||new Set(m.targets).size!==m.targets.length||!m.targets.every(x=>typeof x==='string'&&/^\d{15,22}$/.test(x)&&x!==m.user))throw Error();
  cfg=m;overallTimer=setTimeout(sessionDeadline,600000);connectTimer=setTimeout(()=>finish('CONNECT_TIMEOUT'),12000);
  conn=v.joinVoiceChannel({guildId:m.guild,channelId:m.channel,selfDeaf:false,selfMute:false,daveEncryption:true,debug:false,group:'kristy-native-group-test',adapterCreator:api=>{adapter=api;return{sendPayload,destroy:()=>{}};}});
  conn.on('error',()=>finish('VOICE_ERROR'));
  conn.receiver.speaking.on('start',id=>{if(!cfg.targets.includes(id))finish('OTHER_SPEAKER');});
  conn.on('stateChange',(_,s)=>{
   if(ending)return;
   if(s.status===v.VoiceConnectionStatus.Ready&&!ready&&!encryptionWaiting){
    encryptionWaiting=true;waitEncrypted(conn,{timeoutMs:8000}).then(()=>{if(ending)return;clearTimeout(connectTimer);ready=true;checkSecurity();out({type:'voice_ready'});}).catch(()=>finish('DAVE_WAIT_FAILED'));
   }else if(ready&&s.status!==v.VoiceConnectionStatus.Ready)finish('VOICE_DISCONNECTED');
  });
 }else if(m.type==='ack'){
  const t=acks.get(m.id);if(!t)return;clearTimeout(t);acks.delete(m.id);if(!m.ok)finish('GATEWAY_SEND_FAILED');
 }else if(m.type==='event'){
  if(!cfg||m.data?.guild_id!==cfg.guild)return;
  if(m.kind==='state'&&m.data.user_id===cfg.user){if(m.data.channel_id!==cfg.channel)return finish('VOICE_MOVED');adapter.onVoiceStateUpdate(m.data);}
  else if(m.kind==='server')adapter.onVoiceServerUpdate(m.data);
 }else if(m.type==='listen'){
  if(!ready||listening)throw Error();
  gate=new GroupGate(cfg.targets,gateEvent,finish);listening=true;
  diagnostics=receiveReport.install(conn.receiver);
  if(!diagnostics)return finish('DIAGNOSTIC_CONTRACT_MISMATCH');
  for(const id of cfg.targets){
   const dec=new Worker(path.join(__dirname,'group_decoder.cjs'));
   const source=conn.receiver.subscribe(id,{end:{behavior:v.EndBehaviorType.Manual}});
   const state={stream:source,decoder:dec,pending:0};sources.set(id,state);
   dec.on('error',()=>finish('DECODER_WORKER_ERROR'));
   dec.on('exit',()=>{if(!ending)finish('DECODER_WORKER_EXITED');});
   dec.on('message',m=>{
    let mono;
    try{
     state.pending--;
     if(m.error)return finish('DECODE_ERROR');
     mono=Buffer.from(m.pcm);
     if(!listening||ending)return;
     if(!encrypted(conn))return finish('DAVE_NOT_READY');
     if(!mono.length||mono.length>7680||mono.length%2)return finish('DECODE_ERROR');
     gate.accept(id,mono);
    }catch{finish('DECODE_ERROR');}finally{mono?.fill(0);m.pcm?.fill(0);}
   });
   source.on('error',error=>{receiveFailure=diagnostics?.classify(error)||{receiveStage:'STREAM_OTHER',errorClass:'OTHER'};finish(receiveFailure.receiveStage==='CONTRACT_MISMATCH'?'DIAGNOSTIC_CONTRACT_MISMATCH':'RECEIVE_ERROR');});
   source.on('data',packet=>{
    try{
     if(!listening||ending)return;
     if(!encrypted(conn))return finish('DAVE_NOT_READY');
     if(++state.pending>48)return finish('DECODE_QUEUE_LIMIT');
     packets++;dec.postMessage(packet);
    }catch{finish('DECODE_ERROR');}finally{packet.fill(0);}
   });
  }
  out({type:'listening'});
 }else if(m.type==='output'){
  if(Number.isInteger(m.epoch)&&m.epoch<epoch){droppedChunks++;return;}
  if(m.epoch!==epoch||!epoch||gate.active||!ready||outputEnded||typeof m.data!=='string'||m.data.length>32768||!/^([A-Za-z0-9+/]{4})*([A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(m.data))throw Error();
  if(!encrypted(conn))return finish('DAVE_NOT_READY');
  if(!encoder){encoder=new Opus(48000,2,Opus.Application.AUDIO);outputFilter=new Converter('output');}
  let pcm=Buffer.from(m.data,'base64'),converted;
  try{
   if(pcm.length%2)throw Error();
   if(pcm.length){outputBytes+=pcm.length;turnOutputBytes+=pcm.length;for(let i=0;i<pcm.length;i+=2)if(pcm.readInt16LE(i)!==0){outputNonzeroSamples++;turnNonzero++;}converted=outputFilter.convert(pcm);encodeOutput(converted);}
  }finally{pcm.fill(0);converted?.fill(0);}
 }else if(m.type==='output_end'){if(Number.isInteger(m.epoch)&&m.epoch<epoch){droppedChunks++;return;}if(m.epoch!==epoch||!epoch||gate.active)throw Error();endOutput();}
 else if(m.type==='cancel_output'){if(m.epoch===epoch)cancelOutput();else if(!Number.isInteger(m.epoch)||m.epoch>epoch)throw Error();}
 else if(m.type==='session_deadline')sessionDeadline();
 else if(m.type==='stop')finish();
 else throw Error();
}
const input=readline.createInterface({input:process.stdin});
input.on('line',line=>{try{if(line.length>65536)throw Error();message(JSON.parse(line));}catch{finish('INVALID_COMMAND');}});
input.on('close',()=>finish());process.on('SIGTERM',()=>finish());
process.on('uncaughtException',()=>finish('WORKER_ERROR'));
setTimeout(()=>{if(!cfg)finish('INIT_TIMEOUT');},10000).unref();
