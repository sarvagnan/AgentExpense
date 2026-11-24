import {useEffect,useRef,useState} from 'react';
import {api,request,upload} from '../live/api';
import {rulesApi,type Rule} from '../live/rules-api';
import {RuleEditor} from '../rule-editor';
import {accountLabel, dayLabel, Dialog} from '../ui';
import {formatTimestamp} from '../date-time';
import {requestId} from './api';
import {ReviewEvidence,ReviewHistory} from './weekly-history';
import {issueHref,reviewPost,suggestionPath,type ReviewItem,type ReviewLink,type Suggestion} from './weekly-api';

const titles={ 'review-issue':'Weekly review issue','rule-removal':'Remove this rule?',
  'history-diagnostic':'History needs checking','missing-actor':'Who made this change?' };
export const reviewItemTitle = (item:ReviewItem) => item.title||titles[item.kind];

export function ReviewIssue({id,item,revision,onChange,onClose}:{id:string;item?:ReviewItem;revision?:string;onChange:()=>Promise<void>;onClose:()=>void}) {
  const [form,setForm]=useState<'keep'|'answer'|null>(null),[text,setText]=useState(''),[file,setFile]=useState<File>();
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[stale,setStale]=useState('');
  const [rule,setRule]=useState<{rule:Rule;baseline:Rule;form:Rule;link:ReviewLink}>();
  const [done,setDone]=useState(false);
  const last=useRef(item);
  if(item&&!form&&!rule)last.current=item;
  const current=form||rule?last.current:item||last.current;
  const operation=useRef({signature:'',id:''}),staged=useRef<{id:string;token:string;file:File}|undefined>(undefined),fileInput=useRef<HTMLInputElement>(null);
  const answer=useRef<HTMLTextAreaElement>(null),answerButton=useRef<HTMLButtonElement>(null);
  useEffect(()=>{if(form==='answer')answer.current?.focus();},[form]);

  async function clearUpload(keep?:File) {
    const upload=staged.current;
    if(upload&&upload.file!==keep){
      await api.cancelUpload(upload.id,upload.token);
      staged.current=undefined;
    }
  }
  async function cancelAnswer() {
    setBusy(true);
    try {await clearUpload();setForm(null);setFile(undefined);requestAnimationFrame(()=>answerButton.current?.focus());}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }

  async function act(action:string,actor?:string) {
    setBusy(true);setError('');
    try {
      const path=`review/issues/${encodeURIComponent(id)}/${action}/`;
      let body:Record<string,string>=action==='keep'?{note:text}:action==='answer'?{text}:{};
      if(action==='answer'&&file){
        await clearUpload(file);
        staged.current??={id:(await api.create('',null)).id,token:requestId(),file};
        const {id: draft_id,token}=staged.current;
        await upload(draft_id,file,()=>{},undefined,token);
        body={...body,draft_id};
      } else if(action==='answer') await clearUpload();
      const signature=JSON.stringify([action,body]);
      if(operation.current.signature!==signature)operation.current={signature,id:requestId()};
      if(actor)await reviewPost(`review/unattributed/${encodeURIComponent(id)}`,{actor});
      else await reviewPost(path+(action==='retry'?current!.reply_id:operation.current.id),body);
      operation.current={signature:'',id:''};staged.current=undefined;
      setForm(null);setText('');setFile(undefined);
      if(action==='keep'||actor)setDone(true);
      await onChange();
      if(actor)onClose();
      return true;
    } catch(e){setError((e as Error).message);return false;}finally{setBusy(false);}
  }

  async function apply() {
    const suggestion=current?.suggestion;
    if(!suggestion||suggestion.kind==='question-only')return;
    const link={review_issue_id:id,review_suggestion_id:suggestion.id};
    if(suggestion.kind==='entry'){
      window.location.assign(import.meta.env.BASE_URL+'queue/?'+new URLSearchParams({correct:suggestion.target,back:issueHref(id),...link}));return;
    }
    setBusy(true);setError('');
    try {
      const [checked,{rules}]=await Promise.all([request<Suggestion<Rule>>(suggestionPath(link)),rulesApi.list()]);
      const saved=rules.find(rule=>rule.id===checked.target&&rule.version===checked.version);
      if(!saved)throw new Error('Suggestion is out of date');
      setRule({rule:{...saved,...checked.form},baseline:{...saved,...checked.baseline},form:checked.form,link});
    }catch(e){const message=(e as Error).message;setError(message);if(message==='Suggestion is out of date')setStale(suggestion.id);}
    finally{setBusy(false);}
  }

  const finding=current?.kind==='review-issue',suggestion=current?.suggestion;
  const proposed=suggestion?.kind==='entry'?suggestion.form.entry:suggestion?.kind==='rule'?suggestion.form:null;
  const open=!!item&&!done, waiting=current?.followup==='waiting';
  const decide=current?.decide;
  return <article className="panel draft-detail weekly-issue" aria-label="Review item">
    <div className="panel-header"><div>{current?.first_raised&&<p className="live-hint">Raised before · {formatTimestamp(current.first_raised)}</p>}
      <h2 tabIndex={-1}>{current?reviewItemTitle(current):revision?'Weekly review issue':'Loading review item…'}</h2></div></div>
    <div className="panel-body weekly-activity">
      {waiting&&<p className="live-hint" role="status">Waiting for the reviewer{current?.reason&&` · ${current.reason}`}</p>}
      {current?.followup==='failed'&&<div role="status"><p>Reviewer follow-up failed. {current.reason}</p><button className="button" disabled={busy} onClick={()=>void act('retry')}>Retry</button></div>}
      {current&&<section><h3>{current.kind==='missing-actor'?'Change awaiting attribution':'Concern'}</h3><p>{current.note}</p></section>}
      {current?.source_href&&<section><h3>Evidence</h3><ReviewEvidence files={current.evidence}/>
        <a href={current.source_href}>Cited record</a>{current.links?.map(link=><p key={link.id}><a href={link.href}>Open {link.kind}</a></p>)}
      </section>}
      {(finding||!current)&&<ReviewHistory issue={id} revision={revision} paused={!!(form||rule)}/>}
      {proposed&&<section className="live-notice"><div><h3>Suggested fix</h3><p>{proposed.description} · {dayLabel(proposed.date)}</p>
        <p>{proposed.postings.map(posting=>[accountLabel(posting.account),posting.amount,posting.commodity].join(' ')).join(' · ')}</p></div></section>}
      {finding&&decide&&<section><h3>What to decide</h3><p>{decide}</p>
        {current?.choices&&<ul>{current.choices.map((choice,i)=><li key={i}>{choice}</li>)}</ul>}</section>}
      {current?.kind==='rule-removal'&&<a className="button" href={current.href}>Open Rules</a>}
      {!open&&revision&&(!current||finding)&&<p role="status">This issue is closed or no longer available.</p>}
      {error&&form!=='keep'&&<p className="live-error" role="alert">{error}</p>}
      {form==='answer'&&<form className="weekly-activity" onSubmit={e=>{e.preventDefault();void act('answer');}}>
        <h3>Answer the reviewer</h3><label>Your answer<textarea ref={answer} className="field" maxLength={2000} value={text} disabled={busy} onChange={e=>setText(e.target.value)}/></label>
        <div className="review-action-row"><input ref={fileInput} type="file" hidden aria-label="Choose a supporting file" onChange={e=>setFile(e.target.files?.[0])}/>
          <button type="button" className="button" disabled={busy} onClick={()=>fileInput.current?.click()}>Attach file</button>
          {file&&<><span>{file.name}</span><button type="button" className="button" disabled={busy} aria-label="Remove attached file" onClick={()=>{setFile(undefined);if(fileInput.current)fileInput.current.value='';}}>Remove</button></>}
        </div><div className="review-action-row"><button type="button" className="button" disabled={busy} onClick={()=>void cancelAnswer()}>Cancel</button>
          <button className="button primary" disabled={busy||!text.trim()&&!file}>{busy?'Sending…':'Send answer'}</button></div>
      </form>}
    </div>
    {open&&finding&&form!=='answer'&&<footer className="detail-footer">
      {suggestion&&suggestion.kind!=='question-only'&&<button className="button primary" disabled={busy||stale===suggestion.id} onClick={()=>void apply()}>Apply suggestion</button>}
      <button className="button" disabled={busy} onClick={()=>{setError('');setText('');setForm('keep');}}>Keep as is</button>
      <button className="button" ref={answerButton} disabled={busy||waiting} onClick={()=>{setError('');setText('');setForm('answer');}}>Answer</button>
      <button className="button" disabled={busy||waiting} onClick={()=>void act('explain')}>Explain more</button>
    </footer>}
    {open&&current?.kind==='missing-actor'&&<footer className="detail-footer">
      <button className="button" disabled={busy} onClick={()=>void act('attribute','user')}>You</button>
      <button className="button" disabled={busy} onClick={()=>void act('attribute','agent')}>An agent</button>
    </footer>}
    {form==='keep'&&<Dialog title="Keep as is" labelId="weekly-keep" initialFocus="#weekly-keep-note" onClose={()=>{if(!busy)setForm(null);}}>
      <form onSubmit={e=>{e.preventDefault();void act('keep');}}>
        <div className="settings-body"><p>{current&&reviewItemTitle(current)} · {decide}</p>
          <label>Resolution note (optional)<textarea id="weekly-keep-note" className="field" maxLength={2000} value={text} disabled={busy} onChange={e=>setText(e.target.value)}/></label>
          <p className="live-hint">Your note appears in Review › Change history.</p>{error&&<p className="live-error" role="alert">{error}</p>}</div>
        <footer className="dialog-footer"><button type="button" className="button" disabled={busy} onClick={()=>setForm(null)}>Cancel</button>
          <button className="button primary" disabled={busy}>Keep as is</button></footer>
      </form>
    </Dialog>}
    {rule&&<RuleEditor rule={rule.rule} review={{...rule,explain:()=>act('explain')}} onClose={()=>setRule(undefined)} onSaved={async()=>{setDone(true);await onChange();}}/>}
  </article>;
}
