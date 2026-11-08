import {useEffect} from 'react';
/** Measure the real bars, including wrapping, browser zoom and safe-area padding. */
export function useLiveFocus(view:string){
 useEffect(()=>{
  const root=document.documentElement;
  let frame=0;
  const reveal=()=>{
   const active=document.activeElement as HTMLElement|null;
   if(!active?.closest('.live-page') || active.closest('.add-savebar,.statement-review-action'))return;
   const nav=document.querySelector<HTMLElement>('.phone-live-nav'),bar=document.querySelector<HTMLElement>('.add-savebar,.statement-review-action'),banner=document.querySelector<HTMLElement>('.ledger-check-banner');
   const bottom=Math.min(window.visualViewport?.height||innerHeight,...[nav,bar,banner].filter((e):e is HTMLElement=>!!e && getComputedStyle(e).display!=='none' && getComputedStyle(e).position==='fixed').map(e=>e.getBoundingClientRect().top));
   const box=active.getBoundingClientRect();
   if(box.bottom>bottom-12)window.scrollBy({top:box.bottom-bottom+12,behavior:'instant'});
   else if(box.top<12)window.scrollBy({top:box.top-12,behavior:'instant'});
  };
  const measure=()=>{
   const nav=document.querySelector<HTMLElement>('.phone-live-nav');
   const height=nav&&getComputedStyle(nav).display!=='none'?nav.getBoundingClientRect().height:0;
   root.style.setProperty('--live-nav-height',`${height}px`);
   const bar=document.querySelector<HTMLElement>('.add-savebar,.statement-review-action');
   const saveHeight=bar&&getComputedStyle(bar).position==='fixed'?bar.getBoundingClientRect().height:0;
   root.style.setProperty('--live-save-height',`${saveHeight}px`);
   root.style.setProperty('--live-visible-height',`${window.visualViewport?.height||innerHeight}px`);
   root.style.setProperty('--live-visible-top',`${window.visualViewport?.offsetTop||0}px`);
   // The ledger check's banner is fixed above them: keep focus and the page's end clear of it too.
   const banner=document.querySelector<HTMLElement>('.ledger-check-banner'),bannerHeight=banner?banner.getBoundingClientRect().height+8:0;
   root.style.setProperty('--ledger-banner-height',`${bannerHeight}px`);
   root.style.setProperty('--live-bottom-space',`${height+saveHeight+bannerHeight+24}px`);
   cancelAnimationFrame(frame);frame=requestAnimationFrame(reveal);
  };
  const resize=new ResizeObserver(measure);
  let bars=new Set<Element>();
  const observeBars=()=>{const next=new Set(document.querySelectorAll('.phone-live-nav,.add-savebar,.statement-review-action,.ledger-check-banner'));if(next.size===bars.size&&[...next].every(e=>bars.has(e)))return;for(const e of bars)if(!next.has(e))resize.unobserve(e);for(const e of next)if(!bars.has(e))resize.observe(e);bars=next;measure();};
  const changes=new MutationObserver(observeBars);changes.observe(document.body,{childList:true,subtree:true});observeBars();
  const focus=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(reveal);};
  measure();document.addEventListener('focusin',focus);window.addEventListener('resize',measure);window.visualViewport?.addEventListener('resize',measure);window.visualViewport?.addEventListener('scroll',measure);
  return()=>{changes.disconnect();resize.disconnect();cancelAnimationFrame(frame);document.removeEventListener('focusin',focus);window.removeEventListener('resize',measure);window.visualViewport?.removeEventListener('resize',measure);window.visualViewport?.removeEventListener('scroll',measure);root.style.removeProperty('--live-bottom-space');root.style.removeProperty('--ledger-banner-height');};
 },[view]);
}
