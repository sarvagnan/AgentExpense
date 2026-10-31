/** Storage writes are fallible. Callers must expose failure and offer another copy. */
export function readStored<T>(key: string): T | null {
  try { return JSON.parse(localStorage.getItem(key) || 'null') as T | null; } catch { return null; }
}
export function writeStored(key: string, value: unknown): boolean {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}
export const answerKey = (id: string, index: number, question: string) => `ledger-answer-v1:${id}:${index}:${question}`;
export function validatePiece(key: string, value: string): string {
  const text=value.trim();
  if (!text) return '';
  if (['amount','litres','km','inr'].includes(key) && (!/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(text) || !Number.isFinite(Number(text)) || Number(text)<=0)) return 'Enter a number greater than zero, or leave blank for the agent.';
  if (key==='currency' && (!/^[A-Z]{3}$/.test(text) || !Intl.supportedValuesOf('currency').includes(text))) return 'Enter a three-letter currency code, such as INR or USD, or leave blank.';
  if (key==='date' && (!/^\d{4}-\d{2}-\d{2}$/.test(text) || !Number.isFinite(Date.parse(text)) || new Date(text).toISOString().slice(0,10)!==text || text.startsWith('0000'))) return 'Enter a real date as YYYY-MM-DD, or leave blank.';
  return '';
}

/** Recover the latest editing copy after a failed local ID/clear write. */
export function findRecoveryDraft<T extends {id:string;state:string;text:string;client_updated_at?:number}>(local:{text:string;updatedAt?:number},records:T[]):T|undefined {
 const revision=local.updatedAt||0;
 return [...records].reverse().filter(d=>d.state==='editing' && ((d.client_updated_at||0)>revision || ((d.client_updated_at||0)===revision && (d.text===local.text || (!local.text&&!revision))))).sort((a,b)=>(b.client_updated_at||0)-(a.client_updated_at||0))[0];
}

/** Explicit edits include blank values; parsed typed fields are not manual pins. */
export function restorePieceEdits(pieces:{edits?:unknown;fields?:Record<string,{value:string;source:string}>}|null|undefined):Record<string,string> {
 if(pieces?.edits && typeof pieces.edits==='object' && !Array.isArray(pieces.edits) && Object.values(pieces.edits).every(v=>typeof v==='string'))return {...pieces.edits} as Record<string,string>;
 return Object.fromEntries(Object.entries(pieces?.fields||{}).filter(([,v])=>v.source==='typed').map(([k,v])=>[k,v.value]));
}
