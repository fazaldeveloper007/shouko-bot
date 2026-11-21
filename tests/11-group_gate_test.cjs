'use strict';
const assert=require('node:assert/strict');
const {GroupGate}=require('../voice_live/group_gate.cjs');
let now,events,ends,gate;
function fresh(){now=0;events=[];ends=[];gate=new GroupGate(['A','B'],e=>events.push(e),e=>ends.push(e),()=>now);}
function frame(id,sound=true){now+=20;const b=Buffer.alloc(640);if(sound)for(let i=0;i<640;i+=2)b.writeInt16LE(1000,i);gate.accept(id,b);assert(b.every(x=>x===0));}
function speak(id){for(let i=0;i<3;i++)frame(id);}
function quiet(id,n=65){for(let i=0;i<n;i++)frame(id,false);} // 65*20ms = 1.3s > 1.2s end-of-speech
// 1. Sequential speakers: A's utterance ends after a real pause, B takes epoch 2.
fresh();speak('A');assert.equal(events[0].speaker,'A');assert.equal(events[0].epoch,1);
quiet('A');assert.equal(events.filter(e=>e.type==='speech_end').length,1);
speak('B');assert.equal(events.find(e=>e.type==='speech_start'&&e.speaker==='B').epoch,2);assert.equal(ends.length,0);
// 2. Overlap: B is held back with one content-free notice, the session survives.
fresh();speak('A');speak('B');
assert.deepEqual(ends,[]);assert(!gate.dead);
assert(!events.some(e=>e.speaker==='B'&&e.type!=='overlap_held'));
assert.equal(events.filter(e=>e.type==='overlap_held'&&e.speaker==='B').length,1);
quiet('A');quiet('B'); // A finishes; B's own gate closes and fully clears
speak('B');assert.equal(events.find(e=>e.type==='speech_start'&&e.speaker==='B').epoch,2);
assert.equal(events.filter(e=>e.type==='overlap_held').length,1); // still one notice
// 3. Unknown speakers are ignored, never fatal (they are never subscribed).
fresh();frame('C');assert.deepEqual(ends,[]);assert.equal(events.length,0);
// 4. destroy zeroizes and stops everything.
fresh();gate.destroy();speak('A');assert.equal(events.length,0);
// 5. Speech bursts reset the idle clock.
fresh();for(let i=0;i<5;i++){now+=10000;speak('A');now+=900;gate.tick();}assert(!ends.includes('IDLE_LIMIT'));
// 6. Idle clock paused during playback; fires when idle again.
fresh();gate.setPlaying(true);now=61000;gate.tick();assert.equal(ends.length,0);gate.setPlaying(false);now+=60001;gate.tick();assert.deepEqual(ends,['IDLE_LIMIT']);
// 7. add/remove: a leaving member's open utterance is closed normally first.
fresh();speak('A');gate.remove('A');
assert.equal(events.filter(e=>e.type==='speech_end').length,1);
frame('A');assert(!events.some(e=>e.type==='audio'&&e.epoch===2)); // gone: ignored
gate.add('A');speak('A');
assert.equal(events.filter(e=>e.type==='speech_start').length,2);
assert.equal(events.filter(e=>e.type==='speech_start')[1].epoch,2);
// 8. held: an open utterance closes normally, input pauses, resume is clean.
fresh();speak('A');gate.setHeld(true);
assert.equal(events.filter(e=>e.type==='speech_end').length,1);
speak('A');assert.equal(events.filter(e=>e.type==='speech_start').length,1); // nothing while held
gate.setHeld(false);speak('A');
assert.equal(events.filter(e=>e.type==='speech_start').length,2);
assert.equal(events.filter(e=>e.type==='speech_start')[1].epoch,2);
console.log('group_gate: 8 synthetic speech/identity/overlap/hold/idle/zeroization cases passed');
