import {ActionFeedback} from './pending-actions';
import {useState} from 'react';
import {dayLabel} from '../ui';
import {RuleEditor} from '../rule-editor';
import {type Draft} from './api';
import {type Rule,useRuleOperation,useRules} from './rules-api';
/** Uses the completed draft receipt, independently of Add's layout. */
export function RuleSaved({draft,online,compact=false }:{draft:Draft;online:boolean|null;compact?:boolean}) {
  const {data,refresh}=useRules(online),[editing,setEditing]=useState<Rule|null>(null),[keptPaused,setKeptPaused]=useState<string[]>([]),[more,setMore]=useState(false);
  const op=useRuleOperation(refresh),r=draft.result;
  if(!r?.audit_id||r.outcome==='bill')return null;
  const made=data.rules.filter(rule=>r.made_rules?.includes(rule.id)&&rule.lifecycle!=='removed');
  const resumes=r.resume_rules?.filter(rule=>!keptPaused.includes(rule.id)&&data.rules.some(x=>x.id===rule.id&&x.lifecycle==='paused'))||[];
  if(compact&&!r.usual_payment&&!r.made_rules?.length&&!r.resume_rules?.length&&!r.rule)return null;
  return <article className={`rule-saved ${compact?'rule-saved-inline':'panel'}`} aria-label="Added payment"><header>{!compact&&<h2>Added · {r.description}</h2>}{compact&&<strong>Usual payment saved</strong>}</header>
    {r.next_due&&<p>Next due {dayLabel(data.rules.find(rule=>rule.id===r.rule?.id)?.date||r.next_due)}</p>}
    {r.usual_payment?.estimated&&<p className="rule-conversion">Added at the planned conversion. Statement reconciliation will correct it.</p>}
    {made.map(rule=><section key={rule.id}><p><strong>Made a rule for {rule.description}.</strong></p><div className="rule-actions"><button className="button" disabled={op.busy||online!==true} onClick={()=>setEditing(rule)}>Edit rule</button><button className="button" disabled={op.busy||online!==true} onClick={()=>void op.operate({action:'removed',rule_id:rule.id,version:rule.version})}>Don’t make a rule</button></div></section>)}
    {resumes.map(rule=><section key={rule.id}><p><strong>Resume {rule.description}?</strong></p><div className="rule-actions"><button className="button" disabled={op.busy||online!==true} onClick={()=>void op.operate({action:'resumed',rule_id:rule.id})}>Resume</button><button className="button" disabled={op.busy||online!==true} onClick={()=>{void op.operate({action:'kept',rule_id:rule.id}).then(ok=>{if(ok)setKeptPaused(xs=>[...xs,rule.id]);});}}>Keep paused</button></div></section>)}
    {!compact&&draft.receipt_reading&&<p className="live-hint">{draft.receipt_reading.state==='question'?'The receipt has details to check.':'The payment is saved. Receipt reading continues.'} <a href={`${import.meta.env.BASE_URL}add/?draft=${draft.id}`}>Open receipt details</a></p>}
    {compact&&<button className="text-button" aria-expanded={more} onClick={()=>setMore(!more)}>{more?'Less':'More'}</button>}{(!compact||more)&&<p className="live-hint">The weekly review will look at this payment.</p>}{op.error&&<p className="live-error" role="alert">{op.error}</p>}
    {(!!r.made_rules?.length||!!r.resume_rules?.length)&&<ActionFeedback scope="rules" match={item=>{const id=JSON.parse(item.body).rule_id;return !!r.made_rules?.includes(id)||!!r.resume_rules?.some(rule=>rule.id===id);}}/>}
    {editing&&<RuleEditor rule={editing} onClose={()=>setEditing(null)} onSaved={refresh}/>}
  </article>;
}
