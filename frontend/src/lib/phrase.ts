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
    return t(key as TKey, filled);
  };
}
