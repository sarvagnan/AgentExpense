import {useEffect,useState} from 'react';
import {dayLabel} from '../ui';
import {RuleEditor} from '../rule-editor';
import {type Draft} from './api';
import {type Rule,useRuleOperation,useRules} from './rules-api';
/** Uses the completed draft receipt, independently of Add's layout. */
export function RuleSaved({draft,online}:{draft:Draft;online:boolean|null}) {
  const {data,refresh}=useRules(online),[editing,setEditing]=useState<Rule|null>(null),[undone,setUndone]=useState(false),[keptPaused,setKeptPaused]=useState<string[]>([]),[now,setNow]=useState(Date.now());
  const op=useRuleOperation(refresh),r=draft.result;
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),10000);return()=>clearInterval(timer);},[]);
  if(!r?.audit_id||undone||r.undone)return null;
  const canUndo=r.can_undo&&(!r.undo_until||Date.parse(r.undo_until)>=now);
  const made=data.rules.filter(rule=>r.made_rules?.includes(rule.id)&&rule.lifecycle!=='removed');
  const resumes=r.resume_rules?.filter(rule=>!keptPaused.includes(rule.id)&&data.rules.some(x=>x.id===rule.id&&x.lifecycle==='paused'))||[];
  return <article className="rule-saved panel" aria-label="Added payment"><header><h2>Added · {r.description}</h2>{canUndo&&<button className="button" disabled={op.busy||online!==true} onClick={()=>{void op.operate({action:'undone',undoes:r.audit_id}).then(ok=>{if(ok)setUndone(true);});}}>Undo</button>}</header>
    {r.next_due&&<p>Next due {dayLabel(data.rules.find(rule=>rule.id===r.rule?.id)?.date||r.next_due)}</p>}
    {r.usual_payment?.estimated&&<p className="rule-conversion">Added at the planned conversion. Statement reconciliation will correct it.</p>}
    {made.map(rule=><section key={rule.id}><p><strong>Made a rule for {rule.description}.</strong></p><div className="rule-actions"><button className="button" disabled={op.busy||online!==true} onClick={()=>setEditing(rule)}>Edit rule</button><button className="button" disabled={op.busy||online!==true} onClick={()=>void op.operate({action:'removed',rule_id:rule.id,version:rule.version})}>Don’t make a rule</button></div></section>)}
    {resumes.map(rule=><section key={rule.id}><p><strong>Resume {rule.description}?</strong></p><div className="rule-actions"><button className="button" disabled={op.busy||online!==true} onClick={()=>void op.operate({action:'resumed',rule_id:rule.id})}>Resume</button><button className="button" disabled={op.busy||online!==true} onClick={()=>{void op.operate({action:'kept',rule_id:rule.id}).then(ok=>{if(ok)setKeptPaused(xs=>[...xs,rule.id]);});}}>Keep paused</button></div></section>)}
    <p className="live-hint">The weekly review will look at this payment.</p>{op.error&&<p className="live-error" role="alert">{op.error}</p>}
    {editing&&<RuleEditor rule={editing} onClose={()=>setEditing(null)} onSaved={refresh}/>}
  </article>;
}
