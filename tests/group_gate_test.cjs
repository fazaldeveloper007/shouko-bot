'use strict';
const assert=require('node:assert/strict');
const {GroupGate}=require('../voice_live/group_gate.cjs');
let now,events,ends,gate;
function fresh(){now=0;events=[];ends=[];gate=new GroupGate(['A','B'],e=>events.push(e),e=>ends.push(e),()=>now);}
function frame(id,sound=true){now+=20;const b=Buffer.alloc(640);if(sound)for(let i=0;i<640;i+=2)b.writeInt16LE(1000,i);gate.accept(id,b);assert(b.every(x=>x===0));}
function speak(id){for(let i=0;i<3;i++)frame(id);}
fresh();speak('A');assert.equal(events[0].speaker,'A');assert.equal(events[0].epoch,1);
for(let i=0;i<41;i++)frame('A',false);
speak('B');assert.equal(events.find(e=>e.type==='speech_start'&&e.speaker==='B').epoch,2);assert.equal(ends.length,0);
fresh();speak('A');speak('B');assert.deepEqual(ends,['OVERLAPPING_SPEECH']);assert(gate.dead);assert(!events.some(e=>e.speaker==='B'));
fresh();frame('C');assert.deepEqual(ends,['OTHER_SPEAKER']);assert.equal(events.length,0);
fresh();gate.destroy();speak('A');assert.equal(events.length,0);
fresh();for(let i=0;i<5;i++){now+=10000;speak('A');now+=900;gate.tick();}assert(!ends.includes('IDLE_LIMIT'));
fresh();gate.setPlaying(true);now=61000;gate.tick();assert.equal(ends.length,0);gate.setPlaying(false);now+=60001;gate.tick();assert.deepEqual(ends,['IDLE_LIMIT']);
console.log('group_gate: 6 synthetic speech/identity/overlap/idle/zeroization cases passed');
