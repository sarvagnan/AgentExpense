// Run real hook code with controlled effects, clocks and deferred I/O.
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
export const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
export function hookHarness(file,dependencies={},globals={}){
 const slots=[],effects=[],timers=new Map();let index=0,sequence=0,time=0,dirty=false,renderHook,current;
 const changed=(a,b)=>!a||a.length!==b.length||a.some((x,i)=>!Object.is(x,b[i]));
 const hooks={
  useState(initial){const i=index++;slots[i]??={value:typeof initial==='function'?initial():initial};return [slots[i].value,value=>{const next=typeof value==='function'?value(slots[i].value):value;if(!Object.is(next,slots[i].value)){slots[i].value=next;dirty=true;}}];},
  useRef(initial){const i=index++;return slots[i]??={current:initial};},
  useEffect(fn,deps){const i=index++;if(!slots[i]||changed(slots[i].deps,deps)){const old=slots[i];slots[i]={deps};effects.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});}},
  useMemo(fn,deps){const i=index++;if(!slots[i]||changed(slots[i].deps,deps))slots[i]={deps,value:fn()};return slots[i].value;},
  useCallback(fn,deps){return hooks.useMemo(()=>fn,deps);},
  useLayoutEffect(fn,deps){return hooks.useEffect(fn,deps);},
  // A test passes a context as {value}; without one a component reads null, as with no provider.
  useContext(context){return context&&'value' in context?context.value:null;},
 };
 const schedule=(fn,delay,interval=false)=>{const id=++sequence;timers.set(id,{fn,at:time+delay,delay,interval});return id;};
 const events=new EventTarget(),document=new EventTarget();document.hidden=false;
 const exports={},source=ts.transpileModule(readFileSync(new URL(file,import.meta.url),'utf8').replaceAll('import.meta.env.BASE_URL',JSON.stringify('/expenses/')),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 vm.runInNewContext(source,{exports,AbortController,DOMException,Event,crypto:globalThis.crypto,window:events,document,navigator:{language:'en-US'},setTimeout:(fn,delay)=>schedule(fn,delay),setInterval:(fn,delay)=>schedule(fn,delay,true),clearTimeout:id=>timers.delete(id),clearInterval:id=>timers.delete(id),...globals,require:name=>{if(name==='react')return hooks;if(name in dependencies)return dependencies[name];throw Error(`Missing test dependency ${name} for ${file}`);}});
 function render(fn=renderHook){renderHook=fn;index=0;dirty=false;current=fn(exports);while(effects.length)effects.shift()();return current;}
 async function settle(){for(let n=0;n<20;n++){await new Promise(resolve=>setImmediate(resolve));if(dirty)render();}}
 async function advance(ms){const end=time+ms;for(;;){const due=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;const [id,timer]=due;time=timer.at;if(timer.interval)timer.at+=timer.delay;else timers.delete(id);timer.fn();await settle();}time=end;await settle();}
 return {exports,render,settle,advance,events,document,timers,get current(){return current;},close(){for(const slot of slots)slot?.cleanup?.();}};
}
