import {ruleAmounts,payer} from './recurring-ui.mjs';
/** Sort only the saved rules; currency groups are ordered before native amounts. */
export function sortRules(rules,key,direction,accounts) {
  const compare=(a,b)=>String(a).localeCompare(String(b),undefined,{numeric:true,sensitivity:'base'});
  const amounts=rule=>Object.entries(ruleAmounts(rule)).sort(([a],[b])=>compare(a,b)).map(([currency,amount])=>[currency,Number(rule.bill?.amount??amount)]);
  const account=rule=>{const p=payer(rule,accounts)||rule.postings.find(p=>Number(p.amount)<0);return p?(accounts[p.account]?.label||p.account):'';};
  return [...rules].sort((a,b)=>{
    let order=0;
    if(key==='amount'){
      const left=amounts(a),right=amounts(b);
      // Compare all currency codes before any amount, including split rules.
      order=compare(left.map(([code])=>code).join(','),right.map(([code])=>code).join(','));
      for(let i=0;!order&&i<left.length;i++)order=left[i][1]-right[i][1];
    }else if(key==='name')order=compare(a.description,b.description);
    else if(key==='account')order=compare(account(a),account(b));
    else order=compare(a.next_due||a.date,b.next_due||b.date);
    return (direction==='desc'?-1:1)*(order||compare(a.description,b.description)||compare(a.id,b.id));
  });
}
