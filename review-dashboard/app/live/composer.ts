import { dashboard } from '../data';
import { paymentAccounts } from '../../scripts/account-metadata.mjs';
import {useEffect, useMemo, useRef, useState} from 'react';
import {fetchGzipJson} from '../data';
import {announce} from '../ui';
import {api, ApiError, upload, type Attachment, type Draft} from './api';
import {buildSentence, draftPieces, type PatternData} from './builder';
import {readStored, writeStored, findRecoveryDraft, restorePieceEdits} from './persistence';
import {draftsChanged} from './use-drafts';
const KEY='ledger-add-v1';
type Recovery={updatedAt?:number;id:string|null;text:string;edits:Record<string,string>;uploads?:{key:string;name:string;error:string;cancelled?:boolean}[]};
export type Pending={key:string;name:string;file?:File;progress:number;error:string;busy:boolean;cancelled?:boolean;controller?:AbortController};
const blank=():Recovery=>({id:null,text:'',edits:{}});
function recover():Recovery {const x=readStored<Recovery>(KEY);return x && typeof x.text==='string' && (!x.id || /^[a-f0-9]{32}$/.test(x.id)) && x.edits && typeof x.edits==='object' && Object.values(x.edits).every(v=>typeof v==='string')?x:blank();}
const fingerprint=(f:Recovery)=>JSON.stringify([f.text,f.edits]);
const EMPTY:PatternData={patterns:{},people:[],accounts:[]};
/** Owned by Dashboard, so requests, File objects and recovery outlive Add's route. */
export function useComposer(active:boolean){
  const [form,setForm]=useState(recover),[draft,setDraft]=useState<Draft|null>(null),[patterns,setPatterns]=useState(EMPTY);
  const [pending,setPending]=useState<Pending[]>(()=> (form.uploads||[]).map(p=>({...p,progress:0,busy:false,error:p.error||'Upload interrupted. Choose this file again.'})));
  const [localOK,setLocalOK]=useState(true),[synced,setSynced]=useState(''),[syncError,setSyncError]=useState('');
  const [patternError,setPatternError]=useState(''),[error,setError]=useState(''),[saving,setSaving]=useState(false),[success,setSuccess]=useState(''),[lastSavedId,setLastSavedId]=useState(''),[recovering,setRecovering]=useState(!!form.id);
  const current=useRef(form),uploads=useRef(pending),creation=useRef<Promise<Draft>|null>(null),syncing=useRef<Promise<void>|null>(null),savingRef=useRef(false),generation=useRef(0),fileRequest=useRef(0),restoration=useRef<Promise<void>|null>(null);
  current.current=form;uploads.current=pending;
  const built=useMemo(()=>buildSentence(form.text,{...patterns,modules:dashboard.meta.ledger.modules,payments:paymentAccounts(dashboard.meta.accounts)},form.edits,!!draft?.files.length),[form,patterns,draft?.files.length,dashboard.meta.accounts]);
  const builtRef=useRef(built);builtRef.current=built;
  function persist(next=current.current,items=uploads.current){setLocalOK(writeStored(KEY,{...next,uploads:items.map(p=>({key:p.key,name:p.name,error:p.error,cancelled:p.cancelled}))}));}
  function update(next:Recovery){if(next.text!==current.current.text || JSON.stringify(next.edits)!==JSON.stringify(current.current.edits))next={...next,updatedAt:Math.max(Date.now(),(current.current.updatedAt||0)+1)};current.current=next;setForm(next);setSuccess('');persist(next);}
  function changeUploads(fn:(xs:Pending[])=>Pending[]){const next=fn(uploads.current);uploads.current=next;setPending(next);persist(current.current,next);}
  useEffect(()=>{if(!active)return;let alive=true;void fetchGzipJson<PatternData>('add-patterns').then(x=>{if(alive)setPatterns(x);},()=>{if(alive)setPatternError('History suggestions are unavailable. You can still describe the expense.');});return()=>{alive=false;};},[active]);
  async function ensureDraft():Promise<Draft>{
    await restoration.current;
    if(current.current.id)return api.get(current.current.id);
    if(!creation.current)creation.current=api.create(current.current.text,draftPieces(builtRef.current,current.current.edits),current.current.updatedAt||0).then(d=>{update({...current.current,id:d.id});setDraft(d);draftsChanged();return d;}).finally(()=>{creation.current=null;});
    return creation.current;
  }
  async function sync(){
    await restoration.current;
    if(savingRef.current)return;
    if(syncing.current)return syncing.current;
    if(!current.current.id && !current.current.text.trim())return;
    syncing.current=(async()=>{try{
      const d=await ensureDraft();if(d.state!=='editing')return;
      let copy:Recovery;
      do{copy=current.current;const saved=await api.edit(d.id,copy.text,draftPieces(buildSentence(copy.text,{...patterns,modules:dashboard.meta.ledger.modules,payments:paymentAccounts(dashboard.meta.accounts)},copy.edits,!!d.files.length),copy.edits),copy.updatedAt||0);setSynced(fingerprint(copy));setSyncError('');setDraft(old=>old?.id===saved.id?{...saved,files:old.files}:saved);}while(fingerprint(copy)!==fingerprint(current.current) && !savingRef.current);
    }catch(e){setSyncError(`Could not save to Drafts. ${(e as Error).message}`);}finally{syncing.current=null;}})();
    return syncing.current;
  }
  useEffect(()=>{if(recovering||saving)return;const timer=setTimeout(()=>void sync(),350);return()=>clearTimeout(timer);},[form.text,form.edits,form.id,patterns,recovering,saving]);
  function restore(requested?:string|null):Promise<void>{
    if(restoration.current)return restoration.current.then(()=>requested && requested!==current.current.id?restore(requested):undefined);
    const task=restoreSnapshot(requested).finally(()=>{restoration.current=null;});
    restoration.current=task;return task;
  }
  async function restoreSnapshot(requested?:string|null):Promise<void>{
    const run=++generation.current;
    let id=requested||current.current.id;
    try{
      if(!id){const list=await api.list();if(run!==generation.current || current.current.id)return;id=findRecoveryDraft(current.current,list)?.id||null;}
      if(!id)return;
      setRecovering(true);const ticket=++fileRequest.current;const d=await api.get(id);if(run!==generation.current)return;
      if(d.state==='editing'){
        if(ticket===fileRequest.current)setDraft(d);
        if(id!==current.current.id || (d.client_updated_at||0)>(current.current.updatedAt||0) || (!current.current.text && !Object.keys(current.current.edits).length)){
          const next={id,updatedAt:d.client_updated_at||0,text:d.text,edits:restorePieceEdits(d.pieces)};current.current=next;setForm(next);persist(next);setSynced(fingerprint(next));
        }
        changeUploads(xs=>xs.filter(p=>!d.files.some(f=>f.id===p.key)));
      }else{if(!requested){const latest=findRecoveryDraft(current.current,await api.list());if(latest && latest.id!==id)return restoreSnapshot(latest.id);}update(blank());setDraft(null);setSuccess('Your previous draft is in Drafts.');}
    }catch(e){if(e instanceof ApiError&&e.status===404){if(!requested){const latest=findRecoveryDraft(current.current,await api.list().catch(()=>[]));if(latest)return restoreSnapshot(latest.id);}update({...current.current,id:null});setDraft(null);setError('Your previous draft is no longer available. Your words are kept; choose any files again.');}else if(id) setSyncError(`Could not restore Drafts. ${(e as Error).message}`);}
    finally{if(run===generation.current)setRecovering(false);}
  }
  async function refreshFiles(id:string){const ticket=++fileRequest.current;const d=await api.get(id);if(current.current.id===id && ticket===fileRequest.current)setDraft(d);draftsChanged();}
  async function sendFile(item:Pending){
    changeUploads(xs=>xs.map(p=>p.key===item.key?{...p,busy:true,error:''}:p));
    try{
      const d=await ensureDraft();if(d.state!=='editing')throw Error('This draft has already been saved. Open Drafts.');
      if(item.controller?.signal.aborted)return;
      await upload(d.id,item.file!,progress=>changeUploads(xs=>xs.map(p=>p.key===item.key?{...p,progress}:p)),item.controller?.signal,item.key);
      if(item.controller?.signal.aborted)await api.cancelUpload(d.id,item.key);
      await refreshFiles(d.id);changeUploads(xs=>xs.filter(p=>p.key!==item.key));
    }catch(e){if(!item.controller?.signal.aborted)changeUploads(xs=>xs.map(p=>p.key===item.key?{...p,busy:false,error:(e as Error).message}:p));}
  }
  function pick(files:File[]){if(savingRef.current||recovering)return;const items=files.map(file=>({key:crypto.randomUUID().replaceAll('-',''),name:file.name,file,progress:0,error:'',busy:true,controller:new AbortController()}));changeUploads(xs=>[...xs,...items]);for(const item of items)void sendFile(item);}
  async function cancel(item:Pending){item.cancelled=true;item.controller?.abort();changeUploads(xs=>xs.map(p=>p.key===item.key?{...p,busy:true,cancelled:true}:p));try{const d=await ensureDraft();await api.cancelUpload(d.id,item.key);await refreshFiles(d.id);changeUploads(xs=>xs.filter(p=>p.key!==item.key));}catch(e){changeUploads(xs=>xs.map(p=>p.key===item.key?{...p,busy:false,error:`Could not confirm cancellation. ${(e as Error).message}`}:p));}}
  function retry(item:Pending){if(item.cancelled){void cancel(item);return;}if(!item.file)return;changeUploads(xs=>xs.filter(p=>p.key!==item.key));pick([item.file]);}
  async function remove(file:Attachment){const id=current.current.id!;await api.remove(id,file.id);await refreshFiles(id);}
  async function save(){
    if(savingRef.current||uploads.current.some(p=>p.busy))return;
    savingRef.current=true;setSaving(true);setError('');
    try{await syncing.current;const d=await ensureDraft();if(d.state==='editing'){await api.edit(d.id,current.current.text,draftPieces(builtRef.current,current.current.edits),current.current.updatedAt||0);await api.save(d.id);}else throw Error('This draft has already been sent. Open Drafts to see it.');update(blank());setDraft(null);changeUploads(()=>[]);setSynced('');setLastSavedId(d.id);setSuccess('Saved. The agent is working on it in Drafts.');draftsChanged();announce('Saved to Drafts. Ready for the next expense.');return true;}
    catch(e){setError(`Could not save. ${(e as Error).message}`);return false;}
    finally{savingRef.current=false;setSaving(false);}
  }
  const serverSaved=!!form.id&&fingerprint(form)===synced;
  useEffect(()=>{const guard=(e:BeforeUnloadEvent)=>{if((!localOK&&!serverSaved&&!!form.text)||pending.some(p=>p.busy)){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard);},[localOK,serverSaved,form.text,pending]);
  function retryStorage(){persist();void sync();}
  return {form,draft,patterns:{...patterns,modules:dashboard.meta.ledger.modules,payments:paymentAccounts(dashboard.meta.accounts)},pending,patternError,error,saving,success,lastSavedId,recovering,built,localOK,serverSaved,syncError,update,pick,cancel,retry,remove,save,restore,retryStorage,forgetUpload:(key:string)=>changeUploads(xs=>xs.filter(p=>p.key!==key))};
}
export type Composer=ReturnType<typeof useComposer>;
