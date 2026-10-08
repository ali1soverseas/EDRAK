import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

const CLOSE_MS = 300;

interface OverlayProps {
  /** `drawer` slides in from the inline-end side. `modal` fades in at the centre. */
  variant: "drawer" | "modal";
  /** Called after the closing animation has finished. Unmount the overlay here. */
  onClose: () => void;
  label: string;
  /** The panel content. A function receives `close`, which runs the closing animation. */
  children: ReactNode | ((close: () => void) => ReactNode);
}

const FOCUSABLE = 'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';

/**
 * A scrim and a panel. Esc and a click on the scrim close it. Focus moves into the panel
 * and comes back to where it was. It renders in a portal so the brief's blur and parallax
 * transforms do not become its containing block.
 */
export function Overlay({ variant, onClose, label, children }: OverlayProps) {
  const [shown, setShown] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const closing = useRef(false);

  const requestClose = useCallback(() => {
    if (closing.current) return;
    closing.current = true;
    setShown(false);
    window.setTimeout(onClose, CLOSE_MS);
  }, [onClose]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() => {
      setShown(true);
      const panel = panelRef.current;
      (panel?.querySelector<HTMLElement>("[data-autofocus]") ?? panel)?.focus();
    });
    return () => {
      cancelAnimationFrame(frame);
      previous?.focus?.();
    };
  }, []);

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        requestClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [requestClose]);

  const trapTab = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Tab") return;
    const items = Array.from(panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return createPortal(
    <div className={`ovl ${variant} ${shown ? "open" : ""}`.trim()}>
      <div className="ovl-scrim scrim" onClick={requestClose} />
      <div
        ref={panelRef}
        className="ovl-panel pop"
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        onKeyDown={trapTab}
      >
        {typeof children === "function" ? children(requestClose) : children}
      </div>
    </div>,
    document.body,
  );
}
