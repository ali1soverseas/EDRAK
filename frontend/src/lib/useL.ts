import { useCallback } from "react";
import { useI18n } from "../i18n";

/** Text in both languages, inline: L("Reports", "التقارير"). Used by the product screens. */
export function useL() {
  const { lang } = useI18n();
  return useCallback((en: string, ar: string) => (lang === "ar" ? ar : en), [lang]);
}
