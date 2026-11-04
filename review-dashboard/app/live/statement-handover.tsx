import {useState} from 'react';
import {statementsApi,statementHref} from './statements-api';
/** Handover is a write; the resulting session is a real browser destination. */
export function StatementHandover({draftId}:{draftId:string}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 async function move(){setBusy(true);setError('');try{const s=await statementsApi.handover(draftId);window.location.assign(statementHref(s.id));}catch(e){setError((e as Error).message);setBusy(false);}}
 return <div className="statement-handover"><button className="button primary" disabled={busy} onClick={()=>void move()}>{busy?'Opening Queue…':'Reconcile in Queue'}</button><p className="live-hint">The file moves to a statement session.</p>{error&&<p className="live-error" role="alert">{error}</p>}</div>;
}
