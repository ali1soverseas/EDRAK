import { describe, expect, it } from "vitest";
import { ar } from "./ar";
import { en } from "./en";

const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();

describe("translations", () => {
  const enKeys = Object.keys(en);

  it("has every English key in Arabic, and no Arabic key without an English one", () => {
    const arKeys = new Set(Object.keys(ar));
    expect(enKeys.filter((key) => !arKeys.has(key))).toEqual([]);
    const known = new Set(enKeys);
    const extra = [...arKeys].filter((key) => !known.has(key) && !/\.(zero|two|few|many)$/.test(key));
    expect(extra).toEqual([]);
  });

  it("has no empty strings", () => {
    expect(enKeys.filter((key) => !(en as Record<string, string>)[key].trim())).toEqual([]);
    expect(Object.keys(ar).filter((key) => !ar[key].trim())).toEqual([]);
  });

  it("uses the same placeholders in both languages", () => {
    const mismatched = enKeys.filter((key) => {
      const english = placeholders((en as Record<string, string>)[key]);
      const arabic = placeholders(ar[key]);
      // Arabic plural forms such as "one" and "two" may spell the number out instead of using {n}.
      if (/^plural\./.test(key) && key.endsWith(".one")) return false;
      return JSON.stringify(english) !== JSON.stringify(arabic);
    });
    expect(mismatched).toEqual([]);
  });

  it("gives every Arabic plural every form Arabic uses", () => {
    const bases = new Set(enKeys.filter((key) => key.startsWith("plural.") && key.endsWith(".other")).map((key) => key.replace(/\.other$/, "")));
    expect(bases.size).toBeGreaterThan(5);
    for (const base of bases) {
      for (const form of ["one", "two", "few", "many", "other"]) expect(ar[`${base}.${form}`], `${base}.${form}`).toBeTruthy();
    }
  });

  it("covers every key family the code builds from a worker or an industry", () => {
    const workers = ["int", "comp", "mkt", "cust"];
    const families = ["worker.name", "worker.short", "worker.label", "worker.sub"];
    for (const family of families) for (const worker of workers) expect(en, `${family}.${worker}`).toHaveProperty(`${family}.${worker}`);
    for (const industry of ["fintech", "saas", "retail", "logistics", "other"]) expect(en).toHaveProperty(`company.industry.${industry}`);
    for (const part of ["verification", "synthesis", "brief"]) {
      expect(en).toHaveProperty(`plan.order.${part}.name`);
      expect(en).toHaveProperty(`plan.order.${part}.text`);
    }
  });
});
