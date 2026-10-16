/** True on macOS and iOS, where shortcuts use Command instead of Ctrl. */
export const isMac = (): boolean => {
  const p = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform || navigator.platform || navigator.userAgent;
  return /mac|iphone|ipad|ipod/i.test(p);
};

/** The label of the shortcut modifier on this computer: ⌘ on a Mac, Ctrl elsewhere. */
export const modKey = (): string => (isMac() ? "⌘" : "Ctrl+");

/** The label of the Enter key with the modifier, e.g. "⌘↵" or "Ctrl+Enter". */
export const modEnter = (): string => (isMac() ? "⌘↵" : "Ctrl+Enter");
