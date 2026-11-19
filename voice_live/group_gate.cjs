'use strict';
const {performance}=require('node:perf_hooks');
const {SpeechGate}=require('./speech_gate.cjs');
// Separate decoder/filter per speaker lives in worker. Global epochs bind a
// complete utterance to one actor. Overlap is a safe stop, never merged audio.
class GroupGate {
 constructor(ids,emit,finish,now){
  this.emit=emit;this.finish=code=>{this.destroy();finish(code);};this.dead=false;
  this.now=now||(()=>performance.now());this.last=this.now();this.idle=0;this.playing=false;
  this.epoch=0;this.owner=null;this.gates=new Map();
  for(const id of ids)this.gates.set(id,new SpeechGate(m=>this.event(id,m),this.finish,now));
 }
 event(id,m){
  if(this.dead)return;
  if(m.type==='speech_start'){
   if(this.owner!==null)return this.finish('OVERLAPPING_SPEECH');
   this.owner=id;this.epoch++;
  }
  if(this.owner!==id)return this.finish('GROUP_SPEAKER_MISMATCH');
  if(m.type==='speech_start'||m.type==='audio')this.idle=0;
  this.emit({...m,epoch:this.epoch,speaker:id});
  if(m.type==='speech_end')this.owner=null;
 }
 accept(id,pcm){if(this.dead){pcm.fill(0);return;}const gate=this.gates.get(id);if(!gate){pcm.fill(0);return this.finish('OTHER_SPEAKER');}gate.accept(pcm);}
 get active(){return this.owner!==null;}
 setPlaying(value){this.tick();this.playing=value;for(const gate of this.gates.values())gate.playing=value;}
 tick(){if(this.dead)return;const now=this.now();if(!this.playing)this.idle+=Math.max(0,now-this.last);this.last=now;for(const gate of this.gates.values()){gate.idle=0;gate.lastTick=now;gate.tick();}if(this.idle>=60000)this.finish('IDLE_LIMIT');}
 destroy(){if(this.dead)return;this.dead=true;this.owner=null;for(const gate of this.gates.values())gate.destroy();}
}
module.exports={GroupGate};
