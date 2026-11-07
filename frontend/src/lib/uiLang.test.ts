import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { langHeaders, uiLang } from "./uiLang";

const store = new Map<string, string>();
const memory = {
  getItem: (key: string) => store.get(key) ?? null,
  setItem: (key: string, value: string) => { store.set(key, value); },
  removeItem: (key: string) => { store.delete(key); },
};

beforeEach(() => {
  store.clear();
  (globalThis as { window?: { localStorage: typeof memory } }).window = { localStorage: memory };
});

afterEach(() => {
  store.clear();
});

describe("ui language header", () => {
  it("sends Arabic when the page is in Arabic", () => {
    memory.setItem("edrak.lang", "ar");
    expect(uiLang()).toBe("ar");
    expect(langHeaders().get("X-Edrak-Lang")).toBe("ar");
  });

  it("sends English unless Arabic was chosen", () => {
    memory.setItem("edrak.lang", "en");
    expect(langHeaders().get("X-Edrak-Lang")).toBe("en");
    memory.removeItem("edrak.lang");
    expect(uiLang()).toBe("en");
  });
});
