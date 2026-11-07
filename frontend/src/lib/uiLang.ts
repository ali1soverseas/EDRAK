/** The language the page is showing, read at request time from the same key the switch writes. */
const STORAGE_KEY = "edrak.lang";

export function uiLang(): "en" | "ar" {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "ar" ? "ar" : "en";
  } catch {
    return "en";
  }
}

export function langHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  headers.set("X-Edrak-Lang", uiLang());
  return headers;
}
