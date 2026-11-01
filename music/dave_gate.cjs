'use strict';
// Reads state only. Never logs keys, packets, session IDs or privacy-code values.
function proof(connection){
 const s=connection?.state,n=s?.networking?.state,d=n?.dave;
 return {transportReady:s?.status==='ready' && n?.code===4,
  protocol:Number.isInteger(d?.protocolVersion)?d.protocolVersion:null,
  sessionReady:d?.session?.ready===true,
  privacyPresent:Boolean(connection?.voicePrivacyCode),
  reinitializing:d?.reinitializing===true,
  transitionExecuted:Number.isInteger(d?.lastTransitionId),
  pendingTransitions:d?.pendingTransitions instanceof Map?d.pendingTransitions.size:null};
}
function encrypted(connection){
 const p=proof(connection);
 return p.transportReady && p.protocol>0 && p.sessionReady && p.privacyPresent && !p.reinitializing && p.transitionExecuted && p.pendingTransitions===0;
}
function waitEncrypted(connection,{timeoutMs=10000,intervalMs=50,signal}={}){
 return new Promise((resolve,reject)=>{
  let timer,done=false;const deadline=Date.now()+timeoutMs;
  function finish(error){if(done)return;done=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);error?reject(error):resolve();}
  const abort=()=>finish(Error('DAVE_WAIT_CANCELLED'));
  function check(){
   if(signal?.aborted)return abort();
   if(encrypted(connection))return finish();
   if(['destroyed','disconnected'].includes(connection?.state?.status))return finish(Error('DAVE_CONNECTION_LOST'));
   if(Date.now()>=deadline)return finish(Error('DAVE_WAIT_TIMEOUT'));
   timer=setTimeout(check,intervalMs);
  }
  signal?.addEventListener('abort',abort,{once:true});check();
 });
}
module.exports={proof,encrypted,waitEncrypted};
