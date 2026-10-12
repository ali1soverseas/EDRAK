import { useCallback, useEffect, useState, type RefObject } from "react";

/**
 * Full screen for the brief: the native Fullscreen API on the brief's own element, with a CSS
 * fallback (`.brief-fullscreen`) when the browser refuses. F toggles it; Esc leaves it.
 */
export function useFullscreen(container: RefObject<HTMLElement | null>) {
  const [fullscreen, setFullscreen] = useState(false);

  const toggle = useCallback(async () => {
    if (!fullscreen) {
      try {
        await container.current?.requestFullscreen?.();
      } catch {
        /* the CSS fallback still applies */
      }
      setFullscreen(true);
    } else {
      if (document.fullscreenElement) await document.exitFullscreen().catch(() => undefined);
      setFullscreen(false);
    }
  }, [container, fullscreen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable) return;
      if (e.key.toLowerCase() === "f" && !e.metaKey && !e.ctrlKey && !e.altKey) void toggle();
      if (e.key === "Escape" && fullscreen && !document.querySelector(".ovl")) setFullscreen(false);
    };
    const onChange = () => { if (!document.fullscreenElement) setFullscreen(false); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onChange);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("fullscreenchange", onChange); };
  }, [toggle, fullscreen]);

  return { fullscreen, toggle, setFullscreen };
}
