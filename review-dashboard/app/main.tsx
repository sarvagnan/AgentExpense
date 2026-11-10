import {editOverviewOnLoad,watchEditRestores} from '../scripts/edit-navigation.mjs';
import {PendingActions} from './live/pending-actions';
import { StrictMode, useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Dashboard, { type View } from './dashboard';
import { announce, sessionRead, sessionWrite, useAppearance } from './ui';
import { dashboard, loadData } from './data';
import FirstRun, { needsFirstRun } from './first-run';
import './globals.css';

const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');
const routes: Record<string, View> = {
  [basePath || '/']: 'explorer',
  [`${basePath}/explorer`]: 'explorer',
  [`${basePath}/atlas`]: 'atlas',
  [`${basePath}/journal`]: 'journal',
  [`${basePath}/forecast`]: 'forecast',
  [`${basePath}/review`]: 'review',
  [`${basePath}/account-summary`]: 'accounts',
  [`${basePath}/add`]: 'add',
  [`${basePath}/queue`]: 'queue',
  [`${basePath}/drafts`]: 'queue',
  [`${basePath}/trash`]: 'trash',
  [`${basePath}/statements`]: 'add',
};

function routeFor(pathname: string): View {
  const path = pathname.replace(/\/$/, '') || '/';
  return routes[path] || 'explorer';
}

function App() {
  // Loading and recovery use the same remembered appearance as the dashboard.
  useAppearance();
  // A reload keeps the form's recovery but returns to the Queue overview.
  useState(()=>{if(editOverviewOnLoad(location.search,history.state,(performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming|undefined)?.type))window.history.replaceState({},'',`${basePath}/queue/`);return null;});
  useEffect(()=>watchEditRestores(window,location,history,`${basePath}/queue/`),[]);
  if(window.location.pathname.replace(/\/$/,'')===`${basePath}/statements`)window.history.replaceState({},'',`${basePath}/add/`);
  if(window.location.pathname.replace(/\/$/,'')===`${basePath}/drafts`)window.history.replaceState({},'',`${basePath}/queue/${window.location.search}`);
  const [view, setView] = useState(() => routeFor(window.location.pathname));
  const [load, setLoad] = useState<{ state: 'loading' } | { state: 'ready' } | { state: 'failed'; message: string }>({ state: 'loading' });
  // A ledger with no accounts opens the first-run screens instead of the dashboard.
  const [setup, setSetup] = useState(false);
  const focusAfterSetup = useRef(false);
  const finishSetup = useCallback(() => {
    focusAfterSetup.current = true;
    setSetup(false);
    window.history.pushState({}, '', `${basePath}/add/`);
    setView('add');
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, []);
  // Add has rendered once this runs; its heading takes focus from the removed Finish button.
  useEffect(() => {
    if (setup || !focusAfterSetup.current) return;
    const frame = requestAnimationFrame(() => { focusAfterSetup.current = false; document.querySelector<HTMLElement>('.content h1')?.focus({ preventScroll: true }); });
    return () => cancelAnimationFrame(frame);
  }, [setup]);
  const attempt = useRef(0);
  const previousView = useRef(view);
  const focusAfterRetry = useRef(false);
  useEffect(() => {
    if (load.state !== 'ready' || !focusAfterRetry.current) return;
    const frame = requestAnimationFrame(() => {
      focusAfterRetry.current = false;
      document.querySelector<HTMLElement>('.content h1')?.focus({ preventScroll: true });
      announce('Ledger loaded.');
    });
    return () => cancelAnimationFrame(frame);
  }, [load.state]);
  useEffect(() => {
    if (previousView.current === view) return;
    previousView.current = view;
    // Query changes keep focus; a newly opened modal manages its own focus.
    const frame = requestAnimationFrame(() => {
      if (!document.querySelector('[aria-modal="true"]')) document.querySelector<HTMLElement>('.content h1')?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [view]);

  const retry = useCallback((explicit = false) => {
    focusAfterRetry.current = explicit;
    const current = ++attempt.current;
    setLoad({ state: 'loading' });
    loadData().then(() => { if (attempt.current === current) { setSetup(needsFirstRun()); setLoad({ state: 'ready' }); } }, (error: unknown) => {
      if (attempt.current === current) setLoad({ state: 'failed', message: error instanceof Error ? error.message : String(error) });
    });
  }, []);
  useEffect(() => { retry(); return () => { attempt.current++; }; }, [retry]);

  useEffect(() => {
    const remember = () => sessionWrite(`ledger-view:${routeFor(window.location.pathname)}`, window.location.search);
    const handleHistory = () => { remember(); setView(routeFor(window.location.pathname)); if (dashboard) setSetup(active=>needsFirstRun(active)); };
    remember();
    const handleNavigation = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target instanceof Element ? event.target.closest('a[href]') : null;
      if (!(target instanceof HTMLAnchorElement) || target.target === '_blank' || target.hasAttribute('download')) return;

      const url = new URL(target.href, window.location.href);
      if(url.pathname.replace(/\/$/,'')===`${basePath}/drafts`)url.pathname=`${basePath}/queue/`;
      const path = url.pathname.replace(/\/$/, '') || '/';
      if (url.origin !== window.location.origin || !routes[path]) return;

      event.preventDefault();
      sessionWrite(`ledger-view:${routeFor(window.location.pathname)}`, window.location.search);
      if (routes[path]!=='queue'&&target.closest('nav[aria-label="Views"]')) {
        const remembered = sessionRead(`ledger-view:${routes[path]}`);
        if (remembered !== null) url.search = remembered;
      }
      if (`${url.pathname}${url.search}${url.hash}` !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
        window.history.pushState({}, '', `${url.pathname}${url.search}${url.hash}`);
        window.dispatchEvent(new Event('ledger-navigate'));
      }
      setView(routes[path]);
      if (dashboard && needsFirstRun()) setSetup(true);
      window.scrollTo({ top: 0, behavior: 'instant' });
    };

    window.addEventListener('popstate', handleHistory);
    window.addEventListener('ledger-query-change', remember);
    document.addEventListener('click', handleNavigation);
    return () => {
      window.removeEventListener('popstate', handleHistory);
      window.removeEventListener('ledger-query-change', remember);
      document.removeEventListener('click', handleNavigation);
    };
  }, []);

  if (load.state === 'ready') return <><PendingActions/>{setup ? <FirstRun onDone={finishSetup}/> : <Dashboard view={view} />}</>;
  return <main className="loading-state" aria-busy={load.state === 'loading'}>
    <div role="status" className={load.state === 'failed' ? 'load-failure' : undefined}><span className="wordmark-badge" aria-hidden="true">A</span>{load.state === 'loading' ? <p>Loading ledger data…</p> : <><h1>Ledger unavailable</h1><p>We couldn’t fetch your ledger data. Check your connection and try again.</p><button type="button" className="button primary" onClick={() => retry(true)}>Retry</button><details><summary>Details</summary><p>{load.message}</p><p>If the data files are missing, run <code>npm run data</code>.</p></details></>}</div>
  </main>;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
