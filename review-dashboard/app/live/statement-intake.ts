import {useSyncExternalStore} from 'react';
import {statementsApi as api, type StatementSession} from './statements-api';
// Intake belongs to the app, not the Statements route. Navigation cannot orphan it.
type Intake={name:string;token:string;message:string;error:string;cancelling:boolean;result:StatementSession|null};
let state:Intake={name:'',token:'',message:'',error:'',cancelling:false,result:null};
let controller:AbortController|null=null;
const listeners=new Set<()=>void>();
const publish=(patch:Partial<Intake>)=>{state={...state,...patch};for(const listener of listeners)listener();};
const guard=(e:BeforeUnloadEvent)=>{e.preventDefault();e.returnValue='';};
export const useStatementIntake=()=>useSyncExternalStore(listener=>{listeners.add(listener);return()=>{listeners.delete(listener);};},()=>state);
export async function startStatementUpload(file:File){
 if(state.name||state.cancelling)return;
 const token=crypto.randomUUID().replaceAll('-','');controller=new AbortController();
 publish({name:file.name,token,message:'',error:'',cancelling:false,result:null});window.addEventListener('beforeunload',guard);
 try{const result=await api.upload(file,token,controller.signal);if(state.token===token)publish({result,message:'Statement uploaded. Reading continues on the server.'});}
 catch(e){if(state.token===token)publish({error:(e as Error).message});}
 finally{if(state.token===token){publish({name:''});window.removeEventListener('beforeunload',guard);}}
}
export async function cancelStatementUpload(){
 const token=state.token;if(!token)return;
 controller?.abort();publish({token:'',name:'',message:'Cancelling upload…',error:'',cancelling:true});
 try{await api.cancelUpload(token);publish({message:'Upload cancelled. If it had arrived, its session is in Recently discarded.',result:null});}
 catch(e){publish({error:'Cancellation could not be confirmed. '+(e as Error).message,token});}
 publish({cancelling:false});window.removeEventListener('beforeunload',guard);
}
export const clearStatementIntake=()=>publish({result:null});
