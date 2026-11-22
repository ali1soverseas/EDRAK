import { useI18n, type TKey } from "../i18n";
import { workerKey } from "./workers";

type Params = Record<string, string | number | null | undefined>;

const CATEGORY_AR: Record<string, string> = {
  "internal intelligence": "شركتك",
  "competitor intelligence": "المنافسون",
  "market intelligence": "السوق",
  "customer trends": "العملاء والاتجاهات",
};

/** A stored English sentence, or its dictionary wording when the server sent a key. */
export function usePhrase() {
  const { t, lang } = useI18n();
  return (fallback: string, key?: string | null, params?: Params) => {
    if (!key) return fallback;
    const filled: Record<string, string | number> = {};
    for (const [name, value] of Object.entries(params ?? {})) {
      if (value != null && typeof value !== "object") filled[name] = value;
    }
    if (lang === "ar" && filled.worker_id) {
      filled.worker = t(`worker.label.${workerKey(String(filled.worker_id))}` as TKey);
    }
    if (lang === "ar" && filled.status_code && t(`run.verdict.${filled.status_code}` as TKey) !== `run.verdict.${filled.status_code}`) {
      filled.status = t(`run.verdict.${filled.status_code}` as TKey);
    }
    if (lang === "ar" && typeof filled.category === "string" && CATEGORY_AR[filled.category]) {
      filled.category = CATEGORY_AR[filled.category];
    }
    if (lang === "ar" && typeof filled.change === "string") {
      const changeKey = `alert.change.${filled.change}` as TKey;
      if (t(changeKey) !== changeKey) filled.change = t(changeKey);
    }
    if (lang === "ar" && typeof filled.metric_ar === "string") filled.metric = filled.metric_ar;
    if (key === "brief.fixedVisual" && typeof filled.visual === "string") {
      const visualKey = `visual.${filled.visual}` as TKey;
      const word = t(visualKey);
      if (word !== visualKey) filled.visual = word;
    }
    return t(key as TKey, filled);
  };
}

/** A stored string, or the Arabic side of `{en, ar}` when the page is Arabic. */
export function useShown() {
  const { lang } = useI18n();
  return (value: unknown): string => {
    if (value && typeof value === "object" && !Array.isArray(value) && "en" in (value as object)) {
      const row = value as { en?: string; ar?: string };
      return (lang === "ar" ? row.ar || row.en : row.en) || "";
    }
    return typeof value === "string" ? value : "";
  };
}
