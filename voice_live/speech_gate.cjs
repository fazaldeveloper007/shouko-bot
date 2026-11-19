'use strict';
// Bounded local energy gate, not semantic speech recognition or echo cancellation.
const {performance}=require('node:perf_hooks');
class SpeechGate {
 constructor(emit,finish,now=()=>performance.now()){
  this.emit=emit;this.finish=finish;this.now=now;this.pre=[];this.pending=Buffer.alloc(0);
  this.active=false;this.epoch=0;this.hits=0;this.bytes=0;this.lastVoice=0;this.started=0;
  this.idle=0;this.lastTick=now();this.playing=false;this.dead=false;
 }
 clearPre(){for(const b of this.pre)b.fill(0);this.pre=[];this.pending.fill(0);this.pending=Buffer.alloc(0);}
 accept(pcm){
  if(this.dead){pcm.fill(0);return;}
  const all=Buffer.concat([this.pending,pcm]);this.pending.fill(0);pcm.fill(0);
  let at=0;
  for(;at+640<=all.length&&!this.dead;at+=640)this.frame(Buffer.from(all.subarray(at,at+640)));
  this.pending=this.dead?Buffer.alloc(0):Buffer.from(all.subarray(at));all.fill(0);
 }
 frame(b){
  const now=this.now();let sum=0,peak=0;
  for(let i=0;i<b.length;i+=2){const x=b.readInt16LE(i);sum+=x*x;peak=Math.max(peak,Math.abs(x));}
  const voiced=Math.sqrt(sum/320)>=80&&peak>=200;
  if(!this.active){
   this.pre.push(b);if(this.pre.length>10)this.pre.shift().fill(0);
   this.hits=voiced?this.hits+1:0;
   if(this.hits<3)return;
   this.active=true;this.epoch++;this.bytes=0;this.idle=0;this.lastVoice=now;
   this.started=now-this.pre.length*20;
   this.emit({type:'speech_start',epoch:this.epoch});
   for(const frame of this.pre){this.audio(frame);frame.fill(0);}this.pre=[];
  }else{
   if(voiced){this.lastVoice=now;this.idle=0;}
   this.audio(b);b.fill(0);
  }
  if(this.bytes>=1920000||now-this.started>=60000)this.finish('INPUT_LIMIT');
  else if(now-this.lastVoice>=800)this.end();
 }
 audio(frame){
  const remaining=1920000-this.bytes;
  if(remaining<=0)return;
  this.bytes+=Math.min(frame.length,remaining);
  this.emit({type:'audio',epoch:this.epoch,data:frame.subarray(0,remaining).toString('base64')});
 }
 end(){
  if(!this.active||this.dead)return;
  this.active=false;this.hits=0;this.clearPre();
  this.emit({type:'speech_end',epoch:this.epoch});
 }
 setPlaying(value){this.tick();this.playing=value;}
 tick(){
  if(this.dead)return;
  const now=this.now(),delta=Math.max(0,now-this.lastTick);this.lastTick=now;
  if(this.active){
   if(now-this.started>=60000)return this.finish('INPUT_LIMIT');
   if(now-this.lastVoice>=800)this.end();
  }
  if(!this.playing){this.idle+=delta;if(this.idle>=60000)this.finish('IDLE_LIMIT');}
 }
 destroy(){this.dead=true;this.active=false;this.clearPre();}
}
module.exports={SpeechGate};
