import { useEffect, useRef, type RefObject } from "react";

/**
 * Keeps a scrolling list showing its newest line as lines are added, until the person scrolls up
 * to read something. Scrolling back to the bottom resumes following.
 */
export function useStickToBottom<T extends HTMLElement>(count: number): RefObject<T | null> {
  const ref = useRef<T | null>(null);
  const following = useRef(true);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const onScroll = () => {
      following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 32;
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    return () => element.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const element = ref.current;
    if (element && following.current) element.scrollTop = element.scrollHeight;
  }, [count]);

  return ref;
}
