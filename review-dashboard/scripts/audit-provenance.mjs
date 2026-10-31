import {readdir,readFile} from 'node:fs/promises';
import path from 'node:path';
import {ledgerRoot} from '../../scripts/project-paths.mjs';
/** Publish the compact evidence of creation, leaving runs/tool payloads in the log. */
export async function auditProvenance(root=ledgerRoot()) {
  const directory=path.join(root,'data/additions'), result=new Map(), ids=new Set();
  let files;
  try {files=await readdir(directory);} catch(error) {if(error.code==='ENOENT')return result;throw error;}
  for(const file of files.sort()) {
    if(!/^\d{4}\.jsonl$/.test(file))throw Error(`Invalid audit file: ${file}`);
    for(const line of (await readFile(path.join(directory,file),'utf8')).split('\n').filter(Boolean)) {
      const record=JSON.parse(line);
      if(record.version!==1 || !/^[a-f0-9]{32}$/.test(record.id) || ids.has(record.id))throw Error('Invalid or duplicate audit record');
      ids.add(record.id);
      if(record.kind==='draft' && record.outcome==='added' && record.entry_id) result.set(record.entry_id,{
        auditId:record.id, text:record.text, time:record.time, savedAt:record.client_time || record.saved_at,
        questions:record.questions.map(q=>({question:q.question,answer:q.answer})),
        doubts:record.doubts, models:[...new Set(record.runs.map(r=>`${r.model} · ${r.effort}`))],
      });
    }
  }
  return result;
}
