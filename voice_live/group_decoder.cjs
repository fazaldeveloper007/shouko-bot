'use strict';
// One isolated codec heap per participant. Only Opus in, mono PCM out. No keys,
// network, files, model calls or persistent speaker data. Parent bounds queue.
const {parentPort}=require('node:worker_threads');
const Opus=require('../voice_probe/node_modules/opusscript');
const {Converter}=require('./audio.cjs');
const decoder=new Opus(48000,2,Opus.Application.AUDIO),filter=new Converter('input');
parentPort.on('message',raw=>{
 let packet=Buffer.from(raw),pcm,mono;
 try{
  if(packet.length>4096)throw Error();
  pcm=decoder.decode(packet);
  if(!Buffer.isBuffer(pcm)||!pcm.length||pcm.length>23040||pcm.length%4)throw Error();
  mono=filter.convert(pcm);parentPort.postMessage({pcm:mono});
 }catch{parentPort.postMessage({error:'DECODE_ERROR'});}
 finally{raw.fill(0);packet.fill(0);pcm?.fill(0);mono?.fill(0);}
});
parentPort.on('close',()=>{filter.destroy();decoder.delete();});
