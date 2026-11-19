'use strict';
// Observational receiver-instance wrapper for pinned0.19.2. No crypto changes.
const v=require('../voice_probe/node_modules/@discordjs/voice');
function errorClass(error){
 let name;try{name=error?.name;}catch{}
 switch(name){case 'Error':return 'ERROR';case 'TypeError':return 'TYPE_ERROR';case 'RangeError':return 'RANGE_ERROR';case 'SyntaxError':return 'SYNTAX_ERROR';default:return 'OTHER';}
}
function install(receiver){
 const proto=v.VoiceReceiver.prototype;
 if(v.version!=='0.19.2'||!(receiver instanceof v.VoiceReceiver)||receiver.decrypt!==proto.decrypt||receiver.parsePacket!==proto.parsePacket||proto.decrypt.length!==4||proto.parsePacket.length!==5)return null;
 const decrypt=receiver.decrypt,parse=receiver.parsePacket;
 const ownDecrypt=Object.getOwnPropertyDescriptor(receiver,'decrypt'),ownParse=Object.getOwnPropertyDescriptor(receiver,'parsePacket');
 let failure,stage='RTP_PARSE',restored=false;
 function wrappedDecrypt(...args){
  try{return Reflect.apply(decrypt,this,args);}
  catch(error){stage='TRANSPORT_DECRYPT';throw error;}
 }
 function wrappedParse(...args){
  stage='RTP_PARSE';let dave,original,descriptor,wrapped;
  try{
   const state=this.voiceConnection.state;
   dave=state.status==='ready'&&[4,5].includes(state.networking?.state?.code)?state.networking.state.dave:null;
   if(dave){
    original=dave.decrypt;descriptor=Object.getOwnPropertyDescriptor(dave,'decrypt');
    if(typeof original!=='function'||original.length!==2){stage='CONTRACT_MISMATCH';throw new Error('DIAGNOSTIC_CONTRACT_MISMATCH');}
    wrapped=function(...values){try{return Reflect.apply(original,this,values);}catch(error){stage='DAVE_DECRYPT';throw error;}};
    try{dave.decrypt=wrapped;}catch(error){stage='CONTRACT_MISMATCH';throw error;}
    if(dave.decrypt!==wrapped){stage='CONTRACT_MISMATCH';throw new Error('DIAGNOSTIC_CONTRACT_MISMATCH');}
   }
   return Reflect.apply(parse,this,args);
  }catch(error){failure ||= {error,stage};throw error;}
  finally{
   if(dave&&wrapped&&dave.decrypt===wrapped){if(descriptor)Object.defineProperty(dave,'decrypt',descriptor);else delete dave.decrypt;}
  }
 }
 function undoReceiver(){
  if(receiver.decrypt===wrappedDecrypt){if(ownDecrypt)Object.defineProperty(receiver,'decrypt',ownDecrypt);else delete receiver.decrypt;}
  if(receiver.parsePacket===wrappedParse){if(ownParse)Object.defineProperty(receiver,'parsePacket',ownParse);else delete receiver.parsePacket;}
 }
 try{receiver.decrypt=wrappedDecrypt;receiver.parsePacket=wrappedParse;}catch{undoReceiver();return null;}
 if(receiver.decrypt!==wrappedDecrypt||receiver.parsePacket!==wrappedParse){undoReceiver();return null;}
 return {
  classify(error){return {receiveStage:failure&&failure.error===error?failure.stage:'STREAM_OTHER',errorClass:errorClass(error)};},
  restore(){if(restored)return;restored=true;
   undoReceiver();failure=undefined;
  }
 };
}
module.exports={install,errorClass};
