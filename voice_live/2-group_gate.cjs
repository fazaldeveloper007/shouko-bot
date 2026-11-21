'use strict';
const {performance}=require('node:perf_hooks');
const {SpeechGate}=require('./speech_gate.cjs');
// Separate decoder/filter per speaker lives in worker. Global epochs bind a
// complete utterance to one actor. Speakers are never merged into one stream:
// while one speaker holds the floor, another's overlapping speech is held back
// (dropped, with one content-free notice per epoch) instead of ending the
// session. A held-back speaker takes the floor by pausing first.
class GroupGate {
 constructor(ids,emit,finish,now){
  this.emit=emit;this.finish=code=>{this.destroy();finish(code);};this.dead=false;
  this.now=now||(()=>performance.now());this.last=this.now();this.idle=0;this.playing=false;
  this.epoch=0;this.owner=null;this.gates=new Map();this.suppressed=new Set();
  this.held=false;this.heldLogEpoch=-1;
  for(const id of ids)this.add(id);
 }
 add(id){
  if(this.gates.has(id))return;
  this.gates.set(id,new SpeechGate(m=>this.event(id,m),this.finish,this.now));
 }
 remove(id){
  const gate=this.gates.get(id);
  if(!gate)return;
  // Close an open utterance through the normal path so the other side always
  // sees its matching speech_end before the speaker's gate disappears.
  gate.end();
  gate.destroy();
  this.gates.delete(id);this.suppressed.delete(id);
  if(this.owner===id)this.owner=null;
 }
 event(id,m){
  if(this.dead||this.held)return;
  if(this.suppressed.has(id)){
   // A held-back speaker stays out until they finish their own utterance.
   if(m.type==='speech_end')this.suppressed.delete(id);
   return;
  }
  if(m.type==='speech_start'&&this.owner!==null&&this.owner!==id){
   this.suppressed.add(id);
   this.gates.get(id)?.reset();
   if(this.heldLogEpoch!==this.epoch){this.heldLogEpoch=this.epoch;this.emit({type:'overlap_held',epoch:this.epoch,speaker:id});}
   return;
  }
  if(m.type==='speech_start'){
   this.owner=id;this.epoch++;
  }
  if(this.owner!==id)return; // Straggler frames from a gate that lost the floor.
  if(m.type==='speech_start'||m.type==='audio')this.idle=0;
  this.emit({...m,epoch:this.epoch,speaker:id});
  if(m.type==='speech_end'){this.owner=null;this.suppressed.clear();}
 }
 accept(id,pcm){
  if(this.dead||this.held){pcm.fill(0);return;}
  const gate=this.gates.get(id);
  if(!gate){pcm.fill(0);return;} // Unknown speakers are never subscribed; ignore.
  gate.accept(pcm);
 }
 // While held (consent pause or encryption refresh) all input stops: an open
 // utterance is closed normally first so protocol epochs never dangle.
 setHeld(value){
  if(this.dead||this.held===value)return;
  if(value){
   const owner=this.owner;
   if(owner!==null)this.gates.get(owner)?.end();
   this.held=true;this.owner=null;this.suppressed.clear();
   for(const gate of this.gates.values())gate.reset();
  }else this.held=false;
 }
 get active(){return this.owner!==null;}
 setPlaying(value){this.tick();this.playing=value;for(const gate of this.gates.values())gate.playing=value;}
 tick(){
  if(this.dead||this.held)return;
  const now=this.now();
  if(!this.playing)this.idle+=Math.max(0,now-this.last);
  this.last=now;
  for(const gate of this.gates.values()){gate.idle=0;gate.lastTick=now;gate.tick();}
  if(this.idle>=60000)this.finish('IDLE_LIMIT');
 }
 destroy(){if(this.dead)return;this.dead=true;this.owner=null;this.suppressed.clear();for(const gate of this.gates.values())gate.destroy();}
}
module.exports={GroupGate};
