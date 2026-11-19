const browsingViews = new Set(['explorer', 'atlas', 'journal', 'forecast', 'accounts', 'review']);
const transientParameters = ['edit_changed', 'edited', 'edit_op', 'settings'];

export function sidebarSearch(view, fallback, read) {
  if (!browsingViews.has(view)) return '';
  const params = new URLSearchParams(read(`ledger-view:${view}`) ?? fallback);
  for (const name of transientParameters) params.delete(name);
  return params.toString();
}
