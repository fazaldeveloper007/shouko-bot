'use strict';
// Token-free audio worker. Python owns the sole Discord main gateway and leave.
const readline=require('node:readline'),fs=require('node:fs'),path=require('node:path');
const v=require('@discordjs/voice'),{proof,encrypted,waitEncrypted}=require('./dave_gate.cjs');
const out=m=>process.stdout.write(JSON.stringify(m)+'\n');
let config,connection,methods,player,stream,resource,playingId,ready=false,ending=false,seq=0,readinessAbort,transitionAbort,userPaused=false;
const pending=new Map();
function stopTrack(notify=false){
 const id=playingId;playingId=null;
 userPaused=false;player?.stop(true);stream?.destroy();stream=null;resource=null;player=null;
 if(notify && id)out({type:'ended',id});
}
function shutdown(){
 if(ending)return;ending=true;ready=false;readinessAbort?.abort();transitionAbort?.abort();
 for(const timer of pending.values())clearTimeout(timer);pending.clear();stopTrack();
 try{if(connection && connection.state.status!==v.VoiceConnectionStatus.Destroyed)connection.destroy();}catch{}
 out({type:'stopped'});
}
function fail(code){if(ending)return;out({type:'failure',code});shutdown();}
function sendPayload(payload){
 if(ending || payload.op!==4 || payload.d?.guild_id!==config.guild || payload.d?.channel_id!==config.channel)return false;
 const id=++seq;pending.set(id,setTimeout(()=>fail('GATEWAY_ACK_TIMEOUT'),5000));out({type:'gateway_send',id,payload});return true;
}
function safePath(p){
 if(typeof p!=='string' || path.dirname(path.resolve(p))!==config.mediaDir || !/^track-[a-f0-9]+\.webm$/.test(path.basename(p)))throw Error();
 const stat=fs.lstatSync(p);if(!stat.isFile() || stat.isSymbolicLink() || stat.size>config.maxBytes)throw Error();return p;
}
function suspendForTransition(){
 if(ending || transitionAbort)return;
 player?.pause(true);
 const network=connection?.state?.networking?.state;
 if(network)network.preparedPacket=undefined;
 transitionAbort=new AbortController();out({type:'transition_wait'});
 waitEncrypted(connection,{timeoutMs:10000,signal:transitionAbort.signal}).then(()=>{
  transitionAbort=null;if(ending)return;
  // Fresh packet preparation uses the new executed MLS epoch.
  if(player && !userPaused)player.unpause();
  out({type:'transition_ready'});
 }).catch(()=>{if(!ending)fail('DAVE_TRANSITION_TIMEOUT');});
}
function beginEncryptionWait(){
 if(readinessAbort)return;
 readinessAbort=new AbortController();out({type:'encryption_wait',proof:proof(connection)});
 waitEncrypted(connection,{timeoutMs:10000,signal:readinessAbort.signal}).then(()=>{
  if(ending)return;
  if(!encrypted(connection))return fail('DAVE_READINESS_LOST');
  for(const name of ['prepareAudioPacket','dispatchAudio','playOpusPacket']){
   const original=connection[name].bind(connection);
   connection[name]=(...args)=>{
    if(ending)return;
    if(!encrypted(connection)){suspendForTransition();return;}
    return original(...args);
   };
  }
  ready=true;out({type:'voice_ready',proof:proof(connection)});
 }).catch(()=>{if(!ending){out({type:'encryption_timeout',proof:proof(connection)});fail('DAVE_WAIT_FAILED');}});
}
function message(m){
 if(m.type==='init'){
  if(config || ![m.guild,m.channel,m.user].every(x=>typeof x==='string' && /^\d{15,22}$/.test(x)) || !path.isAbsolute(m.mediaDir) || !Number.isSafeInteger(m.maxBytes) || m.maxBytes<1)throw Error();
  config={...m,mediaDir:path.resolve(m.mediaDir)};
  connection=v.joinVoiceChannel({guildId:m.guild,channelId:m.channel,selfDeaf:true,selfMute:false,daveEncryption:true,debug:false,group:'kristy-music',adapterCreator:api=>{methods=api;return {sendPayload,destroy:()=>{}};}});
  connection.on('error',()=>fail('VOICE_ERROR'));
  connection.on('stateChange',(_,s)=>{
   if(ending)return;
   if(s.status===v.VoiceConnectionStatus.Ready)beginEncryptionWait();
   else if(s.status===v.VoiceConnectionStatus.Disconnected || (ready && s.status!==v.VoiceConnectionStatus.Ready))fail('VOICE_DISCONNECTED');
  });
 }else if(m.type==='ack'){
  const t=pending.get(m.id);if(!t)return;clearTimeout(t);pending.delete(m.id);if(!m.ok)fail('GATEWAY_SEND_FAILED');
 }else if(m.type==='event'){
  if(!config || ending || m.data?.guild_id!==config.guild)return;
  if(m.kind==='state' && m.data.user_id===config.user){if(m.data.channel_id!==config.channel)return fail('VOICE_STATE_CHANGED');methods.onVoiceStateUpdate(m.data);}
  else if(m.kind==='server')methods.onVoiceServerUpdate(m.data);
 }else if(m.type==='play'){
  if(!ready || ending || typeof m.id!=='string' || !/^[a-f0-9]{16}$/.test(m.id))throw Error();
  const p=safePath(m.path);
  // A play that arrives while a track is still marked playing, or while the DAVE
  // session is momentarily not encrypted (e.g. someone joined/left the VC in the
  // idle gap after a skip) replaces/waits instead of killing the whole engine.
  if(playingId)stopTrack();
  playingId=m.id;userPaused=false;
  const begin=()=>{
   if(ending || playingId!==m.id)return;
   stream=fs.createReadStream(p);stream.on('error',()=>fail('MEDIA_READ_FAILED'));
   resource=v.createAudioResource(stream,{inputType:v.StreamType.WebmOpus});
   player=v.createAudioPlayer({behaviors:{noSubscriber:v.NoSubscriberBehavior.Stop}});
   player.on('error',()=>fail('PLAYER_FAILED'));
   player.on('stateChange',(old,s)=>{
    if(s.status===v.AudioPlayerStatus.Playing && playingId)out({type:'playing',id:playingId});
    if(old.status!==v.AudioPlayerStatus.Idle && s.status===v.AudioPlayerStatus.Idle && playingId)stopTrack(true);
   });
   connection.subscribe(player);player.play(resource);
  };
  if(encrypted(connection))begin();
  else waitEncrypted(connection,{timeoutMs:6000}).then(begin).catch(()=>{if(!ending && playingId===m.id){playingId=null;out({type:'play_failed',id:m.id});}});
 }else if(m.type==='pause' || m.type==='resume'){
  if(!player || !playingId) return out({type:'control',id:m.id,ok:false});
  userPaused=m.type==='pause';
  const ok=userPaused?player.pause(true):(transitionAbort?true:player.unpause());out({type:'control',id:m.id,ok});
 }else if(m.type==='stop_track'){stopTrack();out({type:'track_stopped',id:m.id});}
 else if(m.type==='stop'){shutdown();process.exit(0);}
 else throw Error();
}
const input=readline.createInterface({input:process.stdin});
input.on('line',line=>{try{if(line.length>65536)throw Error();message(JSON.parse(line));}catch{fail('INVALID_COMMAND');}});
input.on('close',()=>{shutdown();process.exit(0);});process.on('SIGTERM',()=>{shutdown();process.exit(0);});
