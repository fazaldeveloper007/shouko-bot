'use strict';
// Separate manual alternating session worker. No files or AI keys here.
const readline=require('node:readline');
const {Readable}=require('node:stream');
const v=require('../voice_probe/node_modules/@discordjs/voice');
const Opus=require('../voice_probe/node_modules/opusscript');
const {encrypted,waitEncrypted,proof}=require('../music/dave_gate.cjs');
const {Converter}=require('./audio.cjs');
const out=m=>process.stdout.write(JSON.stringify(m)+'\n');
let turn=0,turnsCompleted=0,awaitNext=true,turnPackets=0,turnInputBytes=0,turnOutputBytes=0,turnNonzero=0;
let cfg,conn,adapter,stream,decoder,encoder,player,resource,inputFilter,outputFilter;
let ending=false,ready=false,listening=false,inputEnded=false,outputEnded=false,startedOutput=false;
let pending=Buffer.alloc(0),frames=[],audioStream,frameSent=0,issued=[],inputBytes=0,outputBytes=0,outputNonzeroSamples=0,packets=0;
let inputTimer,outputTimer,overallTimer,connectTimer,securityTimer,serial=0,encryptionWaiting=false;
const acks=new Map();
function finish(code='STOPPED'){
 if(ending)return;ending=true;listening=false;
 const daveProof=conn?proof(conn):null;
 for(const t of [inputTimer,outputTimer,overallTimer,connectTimer,securityTimer])clearTimeout(t);
 for(const t of acks.values())clearTimeout(t);acks.clear();
 stream?.destroy();stream=null;player?.stop(true);audioStream?.destroy();
 for(const frame of frames.concat(issued))frame.fill(0);frames=[];issued=[];pending.fill(0);pending=Buffer.alloc(0);
 inputFilter?.destroy();outputFilter?.destroy();
 try{decoder?.delete();}catch{}try{encoder?.delete();}catch{}
 try{conn?.destroy();}catch{}
 out({type:'result',code,packets,inputBytes,outputBytes,outputNonzeroSamples,outputFramesRead:frameSent,turnsCompleted,daveProof});
 process.exitCode=['COMPLETE','OUTPUT_LIMIT','STOPPED'].includes(code)?0:1;
 setImmediate(()=>process.exit(process.exitCode));
}
function checkSecurity(){
 if(ending)return;
 if(ready&&!encrypted(conn))return finish('DAVE_NOT_READY');
 securityTimer=setTimeout(checkSecurity,25);
}
function sendPayload(payload){
 if(ending||payload.op!==4||payload.d?.guild_id!==cfg.guild||payload.d?.channel_id!==cfg.channel)return false;
 const id=++serial;acks.set(id,setTimeout(()=>finish('GATEWAY_ACK_TIMEOUT'),3000));
 out({type:'gateway_send',id,payload});return true;
}
function endInput(){
 if(ending||inputEnded)return;listening=false;inputEnded=true;clearTimeout(inputTimer);
 stream?.destroy();stream=null;inputFilter?.destroy();
 out({type:'input_end',turn});
}
function completeIfDone(){
 if(!awaitNext&&outputEnded&&frames.length===0&&player?.state.status===v.AudioPlayerStatus.Idle){
  if(turnOutputBytes>=480000)return finish('OUTPUT_LIMIT');
  awaitNext=true;turnsCompleted++;
  clearTimeout(outputTimer);
  // Remove callbacks before disposing idle player/resources. Never carry codec,
  // PCM, filter history or issued encoded frames into another capture.
  player.removeAllListeners();player.stop(true);player=null;
  audioStream?.destroy();audioStream=null;resource=null;
  for(const f of frames.concat(issued))f.fill(0);frames=[];issued=[];pending.fill(0);pending=Buffer.alloc(0);
  inputFilter?.destroy();outputFilter?.destroy();
  decoder?.delete();encoder?.delete();decoder=encoder=inputFilter=outputFilter=null;
  out({type:'turn_done',turn});
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
  if(s.status===v.AudioPlayerStatus.Playing&&!startedOutput){startedOutput=true;outputTimer=setTimeout(()=>finish('OUTPUT_LIMIT'),10000);out({type:'playing',turn});}
  if(s.status===v.AudioPlayerStatus.Idle)completeIfDone();
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
 if(frames.length>500)return finish('OUTPUT_QUEUE_LIMIT');
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
function message(m){
 if(ending)return;
 if(m.type==='init'){
  if(cfg||![m.guild,m.channel,m.user,m.target].every(x=>typeof x==='string'&&/^\d{15,22}$/.test(x)))throw Error();
  cfg=m;overallTimer=setTimeout(()=>finish('SESSION_LIMIT'),600000);connectTimer=setTimeout(()=>finish('CONNECT_TIMEOUT'),12000);
  conn=v.joinVoiceChannel({guildId:m.guild,channelId:m.channel,selfDeaf:false,selfMute:false,daveEncryption:true,debug:false,group:'kristy-native-live-test',adapterCreator:api=>{adapter=api;return{sendPayload,destroy:()=>{}};}});
  conn.on('error',()=>finish('VOICE_ERROR'));
  conn.receiver.speaking.on('start',id=>{if(id!==cfg.target)finish('OTHER_SPEAKER');});
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
  if(!ready||listening||!awaitNext||m.turn!==turn+1||m.turn>75)throw Error();
  turn=m.turn;awaitNext=false;inputEnded=outputEnded=startedOutput=false;
  turnPackets=turnInputBytes=turnOutputBytes=turnNonzero=0;
  inputFilter=new Converter('input');outputFilter=new Converter('output');
  decoder=new Opus(48000,2,Opus.Application.AUDIO);encoder=new Opus(48000,2,Opus.Application.AUDIO);
  listening=true;inputTimer=setTimeout(endInput,8000);
  stream=conn.receiver.subscribe(cfg.target,{end:{behavior:v.EndBehaviorType.Manual}});
  stream.on('error',()=>finish('RECEIVE_ERROR'));
  stream.on('data',packet=>{
   let pcm,mono;
   try{
    if(!listening||ending)return;
    if(!encrypted(conn))return finish('DAVE_NOT_READY');
    pcm=decoder.decode(packet);if(!Buffer.isBuffer(pcm)||!pcm.length||pcm.length>23040||pcm.length%4)throw Error();
    mono=inputFilter.convert(pcm);
    const remaining=256000-turnInputBytes;
    if(mono.length>remaining){const cut=Buffer.from(mono.subarray(0,remaining));mono.fill(0);mono=cut;}
    if(mono.length){inputBytes+=mono.length;turnInputBytes+=mono.length;packets++;turnPackets++;if(!out({type:'audio',turn,data:mono.toString('base64')}))finish('IPC_BACKPRESSURE');}
    if(turnInputBytes>=256000)endInput();
   }catch{finish('DECODE_ERROR');}finally{pcm?.fill(0);mono?.fill(0);packet.fill(0);}
  });
  out({type:'listening',turn});
 }else if(m.type==='output'){
  if(m.turn!==turn||awaitNext||!inputEnded||!ready||outputEnded||typeof m.data!=='string'||m.data.length>32768||!/^([A-Za-z0-9+/]{4})*([A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(m.data))throw Error();
  if(!encrypted(conn))return finish('DAVE_NOT_READY');
  let pcm=Buffer.from(m.data,'base64'),converted;
  try{
   if(pcm.length%2)throw Error();
   const remaining=480000-turnOutputBytes;
   if(pcm.length>remaining){const cut=Buffer.from(pcm.subarray(0,remaining));pcm.fill(0);pcm=cut;}
   if(pcm.length){outputBytes+=pcm.length;turnOutputBytes+=pcm.length;for(let i=0;i<pcm.length;i+=2)if(pcm.readInt16LE(i)!==0){outputNonzeroSamples++;turnNonzero++;}converted=outputFilter.convert(pcm);encodeOutput(converted);}
   if(turnOutputBytes>=480000)endOutput();
  }finally{pcm.fill(0);converted?.fill(0);}
 }else if(m.type==='output_end'){if(m.turn!==turn||awaitNext||!inputEnded)throw Error();endOutput();}
 else if(m.type==='stop')finish();
 else throw Error();
}
const input=readline.createInterface({input:process.stdin});
input.on('line',line=>{try{if(line.length>65536)throw Error();message(JSON.parse(line));}catch{finish('INVALID_COMMAND');}});
input.on('close',()=>finish());process.on('SIGTERM',()=>finish());
process.on('uncaughtException',()=>finish('WORKER_ERROR'));
setTimeout(()=>{if(!cfg)finish('INIT_TIMEOUT');},10000).unref();
