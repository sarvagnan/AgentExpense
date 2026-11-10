/** Recover the Queue overview when reopening an already-open editor document.
 * Navigation timing can be unavailable; the editor also marks its history entry.
 */
export function editOverviewOnLoad(search,state,navigationType) {
  const params=new URLSearchParams(search);
  return (params.has('correct')||params.get('edit')==='1')&&
    (state?.agentExpenseEditing===true||navigationType==='reload');
}

/** A restored document still has mounted hooks and a released claim: start fresh. */
export function watchEditRestores(target,location,history,queueHref){
 const shown=event=>{if(event.persisted&&(history.state?.agentExpenseEditing===true||editOverviewOnLoad(location.search,history.state,'reload')))location.replace(queueHref);};
 target.addEventListener('pageshow',shown);return()=>target.removeEventListener('pageshow',shown);
}
export function showEntryChangedNotice(search,month){return new URLSearchParams(search).get('edit_changed')===month;}
