import {useCommittedError} from './live/write-completion';
import {useActionGuard} from './live/interaction';
import {retryWrite,writeSnapshot,type WriteOwner} from './live/direct-writes';
import {useRetainedState,clearRetained,retainedSnapshot} from './live/retained-state';
import {ActionFeedback,useWriteOwner} from './live/pending-actions';
import { useEffect, useRef, useState } from 'react';
import currencies from '../../schema/currencies.json';
import { isCurrency } from '../scripts/formatting.mjs';
import { dashboard, dataStatus, reloadData } from './data';
import { Dialog, Icon, announce, money, plural, useAppearance, useDocumentScrollLock, useQueryState } from './ui';
import { request } from './live/api';
import {retentionOperation,type Limits} from './live/retention';
import { AgentNotice, agentIdentity, type AgentStatus } from './live/agent-status';
import { UpdateNotice, knownUpdateStatus, refreshUpdateStatus } from './live/update-notice';

async function saveSetting(path:string,body:unknown,owner:WriteOwner) {
  const result=await request<{data_stale?:boolean;data_pending?:boolean}>(path,{...post(body),writeOwner:owner,clearRetained:retainedSnapshot(path==='settings/name'?['settings-name']:path==='settings/default-currency'?['settings-currency']:[])},120000);
  if(result.data_stale&&!result.data_pending)throw new Error('The change was saved, but dashboard data could not refresh. Retry saving to refresh it.');
}
const post = (body: unknown): RequestInit => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export function CurrencyOptions({current}: {current:string}) {
  return <>{[...new Set([current,...currencies])].map(code=><option key={code} value={code}>{code}</option>)}</>;
}
// A saved setting may precede publication of the dashboard generation. Use its
// acknowledged value while stale; untouched fields never become writes.
function currentSetting(path:string,key:string,fallback:string){
 if(!dataStatus?.data_stale)return fallback;
 const item=writeSnapshot().filter(item=>item.path===path&&item.state==='saved').at(-1);
 try{const value=item&&JSON.parse(item.body)[key];return typeof value==='string'?value:fallback;}catch{return fallback;}
}
export function SettingsDialog({onClose, agent}: {onClose:()=>void;agent:AgentStatus}) {
  useDocumentScrollLock();
  const owner=useWriteOwner('settings');
  const [name,setName]=useRetainedState('settings-name',()=>currentSetting('settings/name','name',dashboard.meta.ledger.name)), [currency,setCurrency]=useRetainedState('settings-currency',()=>currentSetting('settings/default-currency','currency',dashboard.meta.ledger.defaultCurrency));
  const guard=useActionGuard(),[dirty,setDirty]=useRetainedState('settings-dirty',{name:false,currency:false,limits:false});
  const limitRetry=useRef<{signature:string;id:string}|null>(null);
  const [limits,setLimits,initializeLimits]=useRetainedState<Limits>('settings-limits',{delete_days:7,trash_days:7,saved_hours:24}),[original,setOriginal]=useState<Limits|null>(null),[effects,setEffects]=useState<Effects|null>(null),[previewError,setPreviewError]=useState('');
  useEffect(()=>{let alive=true;void request<Limits>('settings/retention').then(value=>{if(alive){if(!dirty.limits)initializeLimits(value);setOriginal(value);}},e=>{if(alive)setPreviewError(e.message);});return()=>{alive=false;};},[]);
  const changed=!!original&&JSON.stringify(limits)!==JSON.stringify(original);
  // Which limit's affected items are listed; a new preview closes the list.
  const [shown,setShown]=useState('');
  useEffect(()=>setShown(''),[effects]);
  useEffect(()=>{if(!changed){setEffects(null);return;}let alive=true;setEffects(null);setPreviewError('');void request<Effects>('settings/retention-preview',post(limits)).then(value=>{if(alive)setEffects(value);},e=>{if(alive)setPreviewError(e.message);});return()=>{alive=false;};},[limits,changed]);
  const [appearance,setAppearance]=useAppearance();
  // The notice uses the status known when Settings opened; a later reply waits for the next opening.
  const [update]=useState(knownUpdateStatus);
  useEffect(()=>{void refreshUpdateStatus();},[]);
  const [busy,setBusy]=useState(false), [error,setError]=useCommittedError(write=>write.path==='settings/name'||write.path==='settings/default-currency'||write.path===`retention/operations/${limitRetry.current?.id}`);
  const otherAccounts=Object.entries(dashboard.meta.accounts).filter(([,meta])=>meta.kind && meta.currency && isCurrency(meta.currency) && meta.currency!==currency);
  const keptCurrencies=[...new Set(otherAccounts.map(([,meta])=>meta.currency))];
  const keptNames=otherAccounts.map(([path,meta])=>meta.label || path.split(':').at(-1));
  const keptNote=otherAccounts.length>3 ? `${otherAccounts.length} existing accounts keep their own currencies.` : keptCurrencies.length===1 ? `${keptNames.length>1 ? keptNames.slice(0,-1).join(', ')+' and ' : ''}${keptNames.at(-1)} kept ${keptCurrencies[0]}.` : `${otherAccounts.map(([path,meta])=>`${meta.label || path.split(':').at(-1)} (${meta.currency})`).join(', ')} keep their own currencies.`;
  const latest=useRef({name,currency,limits});latest.current={name,currency,limits};
  const mounted=useRef(true);useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{const saved=(event:Event)=>{const detail=(event as CustomEvent<{path:string;body:string;previousReason:string}>).detail;if(!detail)return;const body=JSON.parse(detail.body||'{}');if(detail.path==='settings/name'&&latest.current.name.trim()===body.name)setDirty(value=>({...value,name:false}));if(detail.path==='settings/default-currency'&&latest.current.currency===body.currency)setDirty(value=>({...value,currency:false}));if(detail.path.startsWith('retention/operations/')&&body.action==='settings'&&JSON.stringify(latest.current.limits)===JSON.stringify(body.limits)){setOriginal(body.limits);setDirty(value=>({...value,limits:false}));}};window.addEventListener('direct-write-saved',saved);return()=>window.removeEventListener('direct-write-saved',saved);},[]);
  async function save() {
    if (busy || !name.trim()) return;
    const ticket=guard.begin(),submitted=JSON.stringify({name,currency,limits});setBusy(true);setError('');
    try {
      const writes:Promise<void>[]=[];
      if(dirty.name)writes.push(saveSetting('settings/name',{name:name.trim()},owner).then(()=>{if(mounted.current&&latest.current.name===name)setDirty(value=>({...value,name:false}));}));
      if(dirty.currency)writes.push(saveSetting('settings/default-currency',{currency},owner).then(()=>{if(mounted.current&&latest.current.currency===currency)setDirty(value=>({...value,currency:false}));}));
      if(changed||dirty.limits){const signature=JSON.stringify(limits);if(limitRetry.current?.signature!==signature){const kept=writeSnapshot().filter(item=>item.path.startsWith('retention/operations/')&&item.state!=='saved').findLast(item=>{try{const body=JSON.parse(item.body);return body.action==='settings'&&JSON.stringify(body.limits)===signature;}catch{return false;}});limitRetry.current={signature,id:kept?.path.split('/').at(-1)||crypto.randomUUID().replaceAll('-','')};}writes.push(retentionOperation({action:'settings',limits},limitRetry.current.id,owner).then(()=>{setOriginal(limits);if(mounted.current&&JSON.stringify(latest.current.limits)===signature)setDirty(value=>({...value,limits:false}));if(limitRetry.current?.signature===signature)limitRetry.current=null;}));}
      await Promise.all(writes);
      await reloadData();announce('Settings saved.');if(guard.unchanged(ticket)&&JSON.stringify(latest.current)===submitted){clearRetained(['settings-name','settings-currency','settings-dirty','settings-limits']);onClose();}
    } catch(e) {
      // One field may already have committed; reload the actual ledger before retrying.
      await reloadData().catch(()=>undefined);
      setError(`Could not finish saving settings. ${(e as Error).message}`);
    } finally {setBusy(false);}
  }
  const close=()=>{clearRetained(['settings-name','settings-currency','settings-dirty','settings-limits']);onClose();};
  return <Dialog title="Settings" labelId="settings-title" onClose={close} initialFocus="#settings-name"><form className="settings-form" {...guard.interaction} onSubmit={e=>{e.preventDefault();void save();}} aria-busy={busy}>
    <div className="settings-body"><section className="settings-section">
      <label><span className="field-label">Name</span><input id="settings-name" className="field" value={name} maxLength={200} required onChange={e=>{guard.touch();setDirty(value=>({...value,name:true}));setName(e.target.value);}}/></label>
      <label><span className="field-label" id="settings-currency-label">Main currency</span><select aria-labelledby="settings-currency-label" className="field" value={currency} onChange={e=>{guard.touch();setDirty(value=>({...value,currency:true}));setCurrency(e.target.value);}} aria-describedby="main-currency-help"><CurrencyOptions current={currency}/></select></label>
      <p className="live-hint" id="main-currency-help">New accounts start in this currency. Changing it doesn’t change existing accounts.</p>
      {otherAccounts.length>0 && <p className="settings-inset live-hint" data-existing-currencies>{keptNote}</p>}
    </section>
    {/* The agent's status can arrive from a poll while Settings is open. Its slot keeps the
        height of the tallest status, so its arrival moves nothing below it. */}
    <section className="settings-section"><div className="settings-heading-row"><h3>Agent status</h3><span className="live-hint agent-identity">{agentIdentity(agent)}</span></div><div className="agent-status-slot"><AgentNotice status={agent} place="settings"/></div></section>
    <UpdateNotice status={update}/>
    <section className="settings-section retention-settings"><h3>Time limits</h3>{LIMITS.map(([key,label,values])=>timeLimit({key,label,values,unit:key==='saved_hours'?'hours':'days',value:limits[key],disabled:!original,onChange:value=>{guard.touch();setDirty(value=>({...value,limits:true}));setLimits({...limits,[key]:value});},effect:original&&limits[key]!==original[key]?(effects?limitEffect(key,effects):null):undefined,open:shown===key,onToggle:()=>setShown(shown===key?'':key)}))}{previewError&&<p className="live-error" role="alert">{previewError}</p>}<p className="live-hint">Time limits are shared by every device using this ledger.</p></section>
    <section className="settings-section"><button type="button" className="appearance" onClick={()=>setAppearance(appearance==='light'?'dark':'light')}><Icon name={appearance==='light'?'moon':'sun'}/>{appearance==='light'?'Dark':'Light'} appearance</button></section>
    <div className="action-error-slot">{error && <p className="live-error" role="alert">{error}</p>}</div></div>
    <ActionFeedback owner={owner} scope={['settings','retention']} match={item=>item.path.startsWith('settings/')||item.path.startsWith('retention/operations/')&&JSON.parse(item.body).action==='settings'} onRetry={item=>void retryWrite(item.id)}/><footer className="dialog-footer"><button type="button" className="button" onClick={close}>Cancel</button><button type="submit" className="button primary" disabled={busy || !name.trim() || changed&&!effects}>{busy?'Saving…':'Save changes'}</button></footer>
  </form></Dialog>;
}
type Effects={remove_now:{description:string}[];move_to_trash:{description:string}[];deletable_again:{description:string}[]};
const LIMITS=[['delete_days','Delete an entry',[1,3,7,14,30]],['trash_days','Keep in Trash',[1,3,7,14,30]],['saved_hours','Keep saved for later',[12,24,72,168]]] as const;
const limitLabel=(value:number,unit:'days'|'hours')=>unit==='hours'&&value<24?plural(value,'hour'):plural(unit==='hours'?value/24:value,'day');
/** What a changed limit would do now: one sentence, and the affected items on request. */
function limitEffect(key:typeof LIMITS[number][0],effects:Effects):{text:string;items:string[]} {
  const items=(key==='delete_days'?effects.deletable_again:key==='trash_days'?effects.remove_now:effects.move_to_trash).map(item=>item.description),n=items.length;
  const text=key==='delete_days'?(n?`${plural(n,'older entry','older entries')} ${n===1?'becomes':'become'} deletable again.`:'The new window applies to existing entries now.')
    :key==='trash_days'?(n?`${plural(n,'Trash item')} would be removed for good.`:'No Trash items would be removed for good.')
    :(n?`${plural(n,'saved item')} would move to Trash now.`:'No saved items would move to Trash now.');
  return {text,items};
}
/**
 * One limit: its label and selector on one line, and its effect directly under it.
 * The effect slot appears with the change and keeps its height while the preview
 * loads, so the reply moves nothing; the affected items open only on request.
 */
function timeLimit({key,label,values,unit,value,disabled,onChange,effect,open,onToggle}:{key:string;label:string;values:readonly number[];unit:'days'|'hours';value:number;disabled:boolean;onChange:(value:number)=>void;effect?:{text:string;items:string[]}|null;open:boolean;onToggle:()=>void}) {
  const id=`limit-${key}`;
  return <div className="time-limit" key={key}><label className="field-label" htmlFor={id}>{label}</label>
    <select id={id} aria-label={label} className="field" disabled={disabled} value={value} aria-describedby={effect!==undefined?`${id}-effect`:undefined} onChange={e=>onChange(Number(e.target.value))}>{values.map(option=><option key={option} value={option}>{limitLabel(option,unit)}</option>)}</select>
    {effect!==undefined&&<div className="limit-effect live-hint" id={`${id}-effect`} role="status">{effect===null?<p>Checking effects…</p>:<><p>{effect.text}{effect.items.length>0&&<> <button type="button" className="text-button" aria-expanded={open} onClick={onToggle}>{open?'Hide':'Show'} {effect.items.length===1?'it':'them'}</button></>}</p>{open&&<ul>{effect.items.map((item,i)=><li key={i}>{item}</li>)}</ul>}</>}</div>}
  </div>;
}

type AccountDetails = {account:string;label?:string;kind:string;currency:string;currencyEditable:boolean;reason:string|null;openingBalance:string|null};
export function AccountDialog({account,onClose}: {account:string;onClose:()=>void}) {
  useDocumentScrollLock();
  const guard=useActionGuard(),owner=useWriteOwner(`account:${account}`);
  const [selectedAccount,setSelectedAccount]=useQueryState('account','');
  const [details,setDetails]=useState<AccountDetails|null>(null),[currency,setCurrency]=useState(''),[error,setError]=useCommittedError(write=>write.path==='accounts/currency'&&JSON.parse(write.body).account===account),[busy,setBusy]=useState(false);
  useEffect(()=>{const saved=(event:Event)=>{
    const detail=(event as CustomEvent<{id:string;path:string;body:string;previousReason:string}>).detail;
    if(detail?.path!=='accounts/currency')return;
    const job=writeSnapshot().find(item=>item.id===detail.id),body=JSON.parse(detail.body);
    if(body.account!==account||job?.owner?.id!==owner.id&&!job?.owners?.some(value=>value.id===owner.id))return;
    setDetails(value=>value?{...value,currency:body.currency}:value);
    if(selectedAccount.split('|')[0]===account)setSelectedAccount(body.currency===dashboard.meta.ledger.defaultCurrency?account:`${account}|${body.currency}`,'replace');
  };window.addEventListener('direct-write-saved',saved);return()=>window.removeEventListener('direct-write-saved',saved);},[account,owner.id,selectedAccount]);
  useEffect(()=>{const controller=new AbortController();void request<AccountDetails>(`accounts/details?account=${encodeURIComponent(account)}`,{signal:controller.signal}).then(value=>{setDetails(value);const kept=writeSnapshot().findLast(item=>{if(item.path!=='accounts/currency'||item.owner?.id!==owner.id&&!item.owners?.some(value=>value.id===owner.id))return false;try{return JSON.parse(item.body).account===account;}catch{return false;}});setCurrency(kept?JSON.parse(kept.body).currency:value.currency);},e=>{if(!controller.signal.aborted)setError((e as Error).message);});return()=>controller.abort();},[account]);
  async function save() {
    if(!details?.currencyEditable || busy)return;
    const ticket=guard.begin();setBusy(true);setError('');
    try {await saveSetting('accounts/currency',{account,currency},owner);setDetails(value=>value?{...value,currency}:value);if(selectedAccount.split('|')[0]===account)setSelectedAccount(currency===dashboard.meta.ledger.defaultCurrency?account:`${account}|${currency}`,'replace');await reloadData();announce('Account currency saved.');if(guard.unchanged(ticket))onClose();}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  const close=()=>onClose();
  return <Dialog title="Account details" labelId="account-details-title" onClose={close}><form className="settings-form" {...guard.interaction} onSubmit={e=>{e.preventDefault();void save();}} aria-busy={busy}>
    <div className="settings-body">{!details && !error && <p role="status">Loading account…</p>}{details && <>
      <dl className="account-details-facts"><dt>Account name</dt><dd>{details.label || account.split(':').at(-1)}</dd><dt>Kind</dt><dd className="account-kind">{details.kind}</dd></dl>
      {details.currencyEditable ? <><label><span className="field-label" id="account-currency-label">Currency</span><select aria-labelledby="account-currency-label" className="field" value={currency} onChange={e=>{guard.touch();setCurrency(e.target.value);}} aria-describedby="currency-rule"><CurrencyOptions current={currency}/></select></label><p className="live-hint" id="currency-rule">Only an opening balance so far. Changing currency relabels the balance; it doesn’t convert the amount.</p><p className="settings-inset" role="status">Opening balance: {money(Number(details.openingBalance),currency)}. The number stays the same; its currency label changes.</p></>
      : <><dl className="account-details-facts"><dt>Currency</dt><dd data-fixed-currency>{details.currency}</dd></dl><p className="live-hint">This account has entries in {details.currency}, so its currency is fixed.</p></>}
    </>}<div className="action-error-slot">{error && <p className="live-error" role="alert">{error}</p>}</div></div>
    <ActionFeedback owner={owner} scope="settings" path="accounts/currency" match={item=>JSON.parse(item.body).account===account} onRetry={item=>void retryWrite(item.id)}/><footer className="dialog-footer"><button type="button" className="button" onClick={close}>{details?.currencyEditable?'Cancel':'Done'}</button>{details?.currencyEditable && <button className="button primary" disabled={busy || currency===details.currency}>{busy?'Saving…':'Save account'}</button>}</footer>
  </form></Dialog>;
}
