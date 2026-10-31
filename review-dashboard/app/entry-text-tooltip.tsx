import { useState, type FocusEvent } from 'react';
import { createPortal } from 'react-dom';
import { useEscape } from './ui';

/** Keyboard counterpart to cell titles, without adding Tab stops to a row. */
export function useEntryTextTooltip() {
  const [tip, setTip] = useState<{ text: string; left: number; top: number } | null>(null);
  const hide = () => setTip(null);
  useEscape(!!tip, hide);
  return {
    show(event: FocusEvent<HTMLElement>, text: string) {
      if (!event.currentTarget.matches(':focus-visible')) return;
      const box = event.currentTarget.getBoundingClientRect();
      setTip({ text, left: Math.max(8, Math.min(box.left, innerWidth - 488)), top: Math.max(8, Math.min(box.bottom + 6, innerHeight - 180)) });
    },
    hide,
    element: tip && createPortal(<div className="entry-text-tooltip" role="tooltip" style={{ left: tip.left, top: tip.top }}>{tip.text}</div>, document.body),
  };
}
