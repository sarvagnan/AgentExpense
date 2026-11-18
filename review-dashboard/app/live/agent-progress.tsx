import {useEffect,useRef,useState} from 'react';
import {request} from './api';

type Step = {
 type:string; adapter?:string; model?:string; tool?:string; arguments?:string;
 result_bytes?:number|null; text?:string; outcome?:string;
};
type Page = {after:number; events:Step[]};

function stepLabel(step:Step){
 switch(step.type){
  case 'start': return 'Run started';
  case 'prompt': return 'Prompt sent';
  case 'lookup': return 'Lookup · '+step.tool;
  case 'reconnecting': return 'Provider reconnecting';
  case 'progress': return step.text;
  case 'error': return 'Agent error';
  case 'exit': return 'Run ended';
  default: return 'Agent step';
 }
}

export function AgentProgress({path,poll}:{path:string;poll:readonly unknown[]}){
 const [steps,setSteps]=useState<Step[]>([]);
 const read=useRef({after:0,pending:false,control:new AbortController()});
 useEffect(()=>{
  const cursor={after:0,pending:false,control:new AbortController()};
  read.current=cursor;
  setSteps([]);
  return ()=>cursor.control.abort();
 },[path]);
 // A successful Queue refresh supplies a new snapshot. Progress has no timer
 // of its own and a slow read never delays the list or its actions.
 useEffect(()=>{
  const cursor=read.current;
  if(cursor.pending||document.hidden)return;
  cursor.pending=true;
  void request<Page>(path+'/events?after='+cursor.after,{signal:cursor.control.signal}).then(page=>{
   if(cursor.control.signal.aborted)return;
   cursor.after=page.after;
   if(page.events.length)setSteps(previous=>{
    const all=[...previous,...page.events], start=all.findLastIndex(step=>step.type==='start');
    return start>0?all.slice(start):all;
   });
  }).catch(()=>{}).finally(()=>{cursor.pending=false;});
 },[path,poll]);
 const last=steps.at(-1), lookups=steps.filter(step=>step.type==='lookup').length;
 if(!last)return null;
 return <div className="agent-progress">
  <p className="live-hint" role="status" data-reconnecting={last.type==='reconnecting'}>
   {lookups} {lookups===1?'lookup':'lookups'} so far · {stepLabel(last)}
  </p>
  <details><summary>Show steps</summary><ol className="live-hint">
   {steps.map((step,index)=><li key={index}>{stepLabel(step)}
    {step.type==='start'&&` · ${step.adapter} · ${step.model}`}
    {step.type==='lookup'&&` · ${step.arguments}${step.result_bytes==null?'':` · ${step.result_bytes} bytes`}`}
    {step.type==='error'&&step.text&&` · ${step.text}`}
    {step.type==='exit'&&step.outcome&&` · ${step.outcome}`}
   </li>)}
  </ol></details>
 </div>;
}
