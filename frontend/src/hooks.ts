import { KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react';

/** Below this width the sidebar becomes an overlay and the layout tightens. */
export const MOBILE_QUERY = '(max-width: 768px)';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const onChange = () => setMatches(media.matches);
    onChange();
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Open modal dialogs, topmost last (the ingestion modal can open over the drawer). */
const openDialogs: HTMLElement[] = [];

/**
 * Modal dialog behaviour for a component that is mounted while the dialog is open: focus moves into
 * the dialog (give it tabIndex={-1}), Escape closes the topmost dialog, focus stays inside it, and
 * focus returns to the element that opened it.
 */
export function useDialogFocus<T extends HTMLElement>(onClose: () => void) {
  const ref = useRef<T>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    openDialogs.push(node);
    node.focus();
    const isTop = () => openDialogs[openDialogs.length - 1] === node;
    // On the document, not the dialog: focus can drop to <body> (e.g. a clicked button disables itself).
    const onDocumentKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape' && !e.isComposing && isTop()) {
        e.preventDefault();
        onCloseRef.current();
      }
    };
    const onFocusIn = (e: FocusEvent) => {
      if (isTop() && e.target instanceof Node && !node.contains(e.target)) node.focus();
    };
    document.addEventListener('keydown', onDocumentKeyDown);
    document.addEventListener('focusin', onFocusIn);
    return () => {
      document.removeEventListener('keydown', onDocumentKeyDown);
      document.removeEventListener('focusin', onFocusIn);
      openDialogs.splice(openDialogs.indexOf(node), 1);
      if (opener?.isConnected) opener.focus();
      // The opener may be gone or disabled (e.g. the tab switched): fall back to a dialog still open.
      if (!document.activeElement || document.activeElement === document.body) openDialogs[openDialogs.length - 1]?.focus();
    };
  }, []);

  // Tab and Shift+Tab wrap around inside the dialog.
  const onKeyDown = useCallback((e: KeyboardEvent<HTMLElement>) => {
    const root = ref.current;
    if (e.key !== 'Tab' || !root) return;
    const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
    if (items.length === 0) {
      e.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === root)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }, []);

  return { ref, onKeyDown };
}
