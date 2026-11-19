'use strict';
// Fixed labels only. Verified against pinned @discordjs/voice 0.19.2.
// No error messages, stack, codes, packet bytes or cryptographic state leave here.
const v=require('@discordjs/voice');
const installed=new WeakMap();
function errorClass(error) {
 let name;try{name=error?.name;}catch{}
 switch(name){case 'Error':return 'ERROR';case 'TypeError':return 'TYPE_ERROR';
  case 'RangeError':return 'RANGE_ERROR';case 'SyntaxError':return 'SYNTAX_ERROR';default:return 'OTHER';}
}
function install(receiver) {
 if(installed.has(receiver))return installed.get(receiver);
 const proto=v.VoiceReceiver.prototype;
 if(v.version!=='0.19.2'||!(receiver instanceof v.VoiceReceiver)||
    receiver.decrypt!==proto.decrypt||receiver.parsePacket!==proto.parsePacket||
    proto.decrypt.length!==4||proto.parsePacket.length!==5) return null;
 const ownDecrypt=Object.getOwnPropertyDescriptor(receiver,'decrypt');
 const ownParse=Object.getOwnPropertyDescriptor(receiver,'parsePacket');
 const decrypt=receiver.decrypt,parse=receiver.parsePacket;
 let transportThrew=false,transportError,firstFailure,restored=false;
 function wrappedDecrypt(...args) {
  try{return Reflect.apply(decrypt,this,args);}
  catch(error){transportThrew=true;transportError=error;throw error;}
 }
 function wrappedParse(...args) {
  transportThrew=false;transportError=undefined;
  try{return Reflect.apply(parse,this,args);}
  catch(error){
   // First parse failure is the one that destroys the subscribed stream.
   firstFailure ||= {error,code:transportThrew&&error===transportError?
    'RECEIVE_TRANSPORT_ERROR':'RECEIVE_PACKET_PARSE_OR_DAVE_ERROR'};
   throw error;
  }finally{transportThrew=false;transportError=undefined;}
 }
 const diagnostic={
  classify(error){return {code:firstFailure&&firstFailure.error===error?
   firstFailure.code:'RECEIVE_STREAM_ERROR',errorClass:errorClass(error)};},
  restore(){
   if(restored)return;restored=true;
   if(receiver.decrypt===wrappedDecrypt){if(ownDecrypt)Object.defineProperty(receiver,'decrypt',ownDecrypt);else delete receiver.decrypt;}
   if(receiver.parsePacket===wrappedParse){if(ownParse)Object.defineProperty(receiver,'parsePacket',ownParse);else delete receiver.parsePacket;}
   firstFailure=undefined;transportError=undefined;installed.delete(receiver);
  }
 };
 // Override only this private probe receiver, never the class prototype.
 receiver.decrypt=wrappedDecrypt;receiver.parsePacket=wrappedParse;
 installed.set(receiver,diagnostic);return diagnostic;
}
module.exports={install,errorClass};
