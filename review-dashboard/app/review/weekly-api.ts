import type {Rule} from '../live/rules-api';
import {request, type AgentPause, type Proposal} from '../live/api';

export type Evidence = {path:string;href?:string};
export type ReviewEvent = {
  id:string; kind:string; time:string; actor?:string; text?:string; note?:string; reason?:string;
  decide?:string; entry_id?:string; rule_id?:string; message_evidence?:Evidence[];
  review_issue_id?:string; source_href?:string; title?:string;
};
export type HistoryPage = {items:ReviewEvent[]; next_offset:number|null};
export type Reviewer = {adapter:string; model:string; effort:string};
type Report = {
  fine:{number:number; reason:string; lines:string[]}[];
  decisions:{number:number; id:string; issue_id:string; href:string; source_href:string; resolution_href?:string;
    status:string; decide:string; evidence:Evidence[]; title?:string; note?:string; first_raised?:string; suggestion?:string}[];
  patterns:{number:number; note:string; suggested_change?:string|null}[];
};
export type Run = Reviewer & {run_id:string; checked:number; issues:number; carried:number; rule_proposals:number;
  period:[string|null,string|null]; history_errors?:string[]; missing_actors?:string[]};
export type ActiveRun = Run & {state:string; reason?:string; stage?:string; retry_action?:string; batch_count:number; committed_batches:number};
export type FinishedRun = Run & {ended:string; status:string; report:Report|null};
export type WeeklyStatus = {enabled:boolean; day:string; active:ActiveRun|null; history:FinishedRun[];
  next_offset:number|null; gate:{state:string; reason?:string}; agent_pause:AgentPause|null};
export type ReviewLink = {review_issue_id:string; review_suggestion_id:string};
export type Suggestion<T> = {kind:'entry'|'rule'; id:string; target:string; form:T; version:string; baseline:T};
export type ReviewItem = {
  id:string; kind:'review-issue'|'rule-removal'|'history-diagnostic'|'missing-actor'; note:string; title?:string;
  time?:string; first_raised?:string; decide?:string; choices?:string[]; evidence?:Evidence[];
  links?:{kind:string; id:string; href:string}[]; href?:string; source_href?:string; rule_id?:string;
  suggestion?:{kind:'question-only';reason?:string}|{kind:'entry';id:string;target:string;form:Proposal}|{kind:'rule';id:string;target:string;form:Rule};
  followup?:'waiting'|'failed'; reply_id?:string; reason?:string;
};
export type ReviewQueue = {revision:string; items:ReviewItem[]; needs_you:number; history?:HistoryPage};
export const suggestionPath = ({review_issue_id,review_suggestion_id}:ReviewLink) =>
  `review/issues/${encodeURIComponent(review_issue_id)}/suggestion/${encodeURIComponent(review_suggestion_id)}`;
export const issueHref = (id:string) => `${import.meta.env.BASE_URL}queue/?review_issue_id=${encodeURIComponent(id)}`;
export const reviewPost = <T,>(path:string,body:object) => request<T>(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},180000);
