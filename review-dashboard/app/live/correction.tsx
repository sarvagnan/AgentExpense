import {useEffect,useState} from 'react';
import {reloadData} from '../data';
import {dayLabel} from '../ui';
import {api,type Draft,type CorrectionResult} from './api';
import HandFinish from './hand-finish';
import {draftsChanged} from './use-drafts';

export default function Correction({entryId,onBack,backLabel='Back',initialDraft}:{entryId:string;onBack:()=>void;backLabel?:string;initialDraft?:Draft}) {
  const [draft,setDraft]=useState<Draft|null>(initialDraft||null),[error,setError]=useState(''),[result,setResult]=useState<CorrectionResult|null>(null);
  useEffect(()=>{if(initialDraft)return;let alive=true;void api.correctionForm(entryId).then(d=>{if(alive)setDraft(d);},e=>{if(alive)setError(e.message);});return()=>{alive=false;};},[entryId]);
  if(result)return <article className="panel draft-detail"><div className="panel-body"><h2>Correction saved</h2><p>{result.description}</p>{result.rule.status==='kept'?<p>Still linked to {result.rule.description} · next due {dayLabel(result.rule.next_due!)}</p>:result.rule.status==='dropped'?<p>No longer linked to {result.rule.description} · next due {dayLabel(result.rule.next_due!)}</p>:null}{result.data_stale&&<p role="status">Saved. Dashboard refresh is still needed.</p>}<p>To change this again, open Correct this entry.</p><button className="button" onClick={onBack}>{backLabel}</button></div></article>;
  return draft?<HandFinish draft={draft} backLabel={backLabel} onDone={async()=>onBack()} onCorrected={r=>{setResult(r);void reloadData().catch(()=>setError('Saved. Dashboard refresh is still needed.'));draftsChanged();}}/>:<article className="panel draft-detail"><div className="panel-body"><h2>Correct this entry</h2>{error?<p role="alert">{error}</p>:<p role="status">Opening the saved entry…</p>}<button className="button" onClick={onBack}>{backLabel}</button></div></article>;
}
