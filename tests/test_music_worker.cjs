'use strict';
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict'),{EventEmitter}=require('node:events'),path=require('node:path');
const gate=require('../music/dave_gate.cjs');
const source=fs.readFileSync(path.join(__dirname,'../music/worker.cjs'),'utf8');
const native=require('../music/node_modules/@discordjs/voice');
async function run(mode){
 const input=new EventEmitter(),c=new EventEmitter();let packets=[],sends=0,player,resource,stopped=false;
 const d={protocolVersion:1,session:{ready:false},reinitializing:false,lastTransitionId:undefined,pendingTransitions:new Map()};
 c.state={status:'signalling',networking:{state:{code:4,dave:d}}};let privacy=false;
 Object.defineProperty(c,'voicePrivacyCode',{get:()=>privacy?'SYNTHETIC':''});c.destroy=()=>{c.state.status='destroyed';};c.subscribe=()=>{};
 for(const name of ['prepareAudioPacket','dispatchAudio','playOpusPacket'])c[name]=()=>{sends++;return true;};
 const v={VoiceConnectionStatus:native.VoiceConnectionStatus,AudioPlayerStatus:native.AudioPlayerStatus,NoSubscriberBehavior:native.NoSubscriberBehavior,StreamType:native.StreamType,
  joinVoiceChannel:o=>{o.adapterCreator({onVoiceStateUpdate:()=>{},onVoiceServerUpdate:()=>{}});return c;},
  createAudioResource:s=>{resource={stream:s};return resource;},
  createAudioPlayer:()=>{player=new EventEmitter();player.play=()=>{player.emit('stateChange',{status:'idle'},{status:'playing'});};player.stop=()=>{};player.pause=()=>true;player.unpause=()=>true;return player;}};
 const stream=()=>({on(){return this;},destroy(){this.destroyed=true;}});
 const safeFs={lstatSync:()=>({isFile:()=>true,isSymbolicLink:()=>false,size:123}),createReadStream:stream};
 const sandbox={AbortController,Map,require:n=>n==='@discordjs/voice'?v:n==='./dave_gate.cjs'?{...gate,waitEncrypted:(c,o)=>gate.waitEncrypted(c,{...o,timeoutMs:40,intervalMs:2})}:n==='node:fs'?safeFs:n==='node:readline'?{createInterface:()=>input}:require(n),process:{stdin:{},stdout:{write:s=>packets.push(JSON.parse(s))},on:()=>{},exit:()=>{stopped=true}},setTimeout,clearTimeout};
 vm.runInNewContext(source,sandbox);const send=m=>input.emit('line',JSON.stringify(m));
 if(mode==='invalid_init'){send({type:'init',guild:'bad'});assert(packets.some(p=>p.code==='INVALID_COMMAND'));return;}
 send({type:'init',guild:'111111111111111111',channel:'222222222222222222',user:'333333333333333333',mediaDir:'/tmp/media-test',maxBytes:1024});
 c.state.status='ready';c.emit('stateChange',{},c.state);assert(!packets.some(p=>p.type==='voice_ready'));
 if(mode==='timeout'){await new Promise(r=>setTimeout(r,60));assert(packets.some(p=>p.code==='DAVE_WAIT_FAILED'));return;}
 d.session.ready=true;d.lastTransitionId=0;privacy=true;await new Promise(r=>setTimeout(r,8));assert(packets.some(p=>p.type==='voice_ready'));
 if(mode==='path_escape'){send({type:'play',id:'1234567890abcdef',path:'/tmp/outside.webm'});assert(packets.some(p=>p.code==='INVALID_COMMAND'));return;}
 for(let i=0;i<2;i++){
  send({type:'play',id:'1234567890abcdef',path:'/tmp/media-test/track-1234.webm'});assert(packets.some(p=>p.type==='playing'));
  send({type:'pause',id:'pause'});send({type:'resume',id:'resume'});assert(packets.filter(p=>p.type==='control' && p.ok).length>=2);
  if(i===0){send({type:'stop_track',id:'stop'});assert(packets.some(p=>p.type==='track_stopped'));}
 }
 c.prepareAudioPacket();assert.equal(sends,1);d.session.ready=false;c.dispatchAudio();assert.equal(sends,1);assert(!packets.some(p=>p.type==='failure'));assert(packets.some(p=>p.type==='transition_wait'));
 if(mode==='transition_timeout'){await new Promise(r=>setTimeout(r,60));assert(packets.some(p=>p.code==='DAVE_TRANSITION_TIMEOUT'));}
 else{d.session.ready=true;d.lastTransitionId=1;await new Promise(r=>setTimeout(r,8));assert(packets.some(p=>p.type==='transition_ready'));c.dispatchAudio();assert.equal(sends,2);}
 send({type:'stop'});assert(stopped);
}
(async()=>{
 const d=new native.DAVESession(1,'333333333333333333','222222222222222222',{});d.reinit();assert.equal(d.session.ready,false);assert(!d.voicePrivacyCode);d.destroy();console.log('PASS real native initial-not-ready');
 for(const mode of ['invalid_init','timeout','path_escape','two_tracks_controls_transition_resume','transition_timeout']){await run(mode);console.log('PASS actual worker',mode);}
 console.log('NO_LOGIN_NO_NETWORK_WORKER_SUITE_PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
