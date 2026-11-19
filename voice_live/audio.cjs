'use strict';
// Integer-ratio streaming FIR conversion. Synthetic-only tests; no audio files.
const clamp=x=>Math.max(-32768,Math.min(32767,Math.round(x)));
class Converter {
 constructor(kind){
  if(!['input','output'].includes(kind))throw Error('CONVERTER_KIND');
  this.kind=kind;this.factor=kind==='input'?3:2;this.taps=kind==='input'?63:47;
  const cutoff=kind==='input'?7000/48000:10500/48000;
  this.coeffs=Array.from({length:this.taps},(_,i)=>{
   const x=i-(this.taps-1)/2;
   return (x===0?2*cutoff:Math.sin(2*Math.PI*cutoff*x)/(Math.PI*x))*(0.54-0.46*Math.cos(2*Math.PI*i/(this.taps-1)));
  });
  const sum=this.coeffs.reduce((a,b)=>a+b,0);
  this.coeffs=this.coeffs.map(x=>x/sum*(kind==='output'?2:1));
  this.history=new Float64Array(this.taps);this.at=0;this.phase=0;
 }
 filter(x){
  this.history[this.at]=x;let value=0;
  for(let k=0;k<this.taps;k++)value+=this.coeffs[k]*this.history[(this.at-k+this.taps)%this.taps];
  this.at=(this.at+1)%this.taps;return clamp(value);
 }
 convert(buffer){
  if(!Buffer.isBuffer(buffer)||buffer.length%(this.kind==='input'?4:2))throw Error('PCM_ALIGNMENT');
  const values=[];
  if(this.kind==='input'){
   for(let i=0;i<buffer.length;i+=4){const v=this.filter((buffer.readInt16LE(i)+buffer.readInt16LE(i+2))/2);if(this.phase++%3===0)values.push(v);}
  }else{
   for(let i=0;i<buffer.length;i+=2){values.push(this.filter(buffer.readInt16LE(i)),this.filter(0));}
  }
  const out=Buffer.alloc(values.length*(this.kind==='input'?2:4));
  values.forEach((v,i)=>{if(this.kind==='input')out.writeInt16LE(v,i*2);else{out.writeInt16LE(v,i*4);out.writeInt16LE(v,i*4+2);}});
  values.fill(0);return out;
 }
 destroy(){this.history.fill(0);this.coeffs.fill(0);this.at=this.phase=0;}
}
module.exports={Converter};
