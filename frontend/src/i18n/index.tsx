import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { Lang } from "../types/app";
import { en, type TKey } from "./en";
import { ar } from "./ar";

const STORAGE_KEY = "edrak.lang";

const dictionaries: Record<Lang, Record<string, string>> = { en, ar };

/** Latin digits in Arabic too, so evidence chips, times and counts read the same in both. */
const LOCALES: Record<Lang, string> = { en: "en-GB", ar: "ar-EG-u-nu-latn" };

type Params = Record<string, string | number>;

function interpolate(template: string, params?: Params): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

/** Keys that have `.one` / `.other` (and for Arabic `.two` / `.few` / `.many`) variants. */
export type PluralKey =
  | "plural.workers"
  | "plural.failedWorkers"
  | "plural.tasks"
  | "plural.taskUnit"
  | "plural.workerUnit"
  | "plural.sources"
  | "plural.findings"
  | "plural.gapsInfo";

export interface I18n {
  lang: Lang;
  dir: "ltr" | "rtl";
  locale: string;
  setLang: (lang: Lang) => void;
  t: (key: TKey, params?: Params) => string;
  /** Pluralised text such as "3 sources". `{n}` is filled in. */
  tn: (key: PluralKey, n: number, params?: Params) => string;
  formatTime: (iso: string) => string;
  formatDate: (iso: string) => string;
  formatDateTime: (iso: string) => string;
  /**
   * "12 minutes ago", "Today, 09:44", "Yesterday, 16:20" or "30 Sep, 08:14", depending on how
   * long ago. `inline` lowercases the first word so it reads inside a sentence.
   */
  formatWhen: (iso: string, options?: { now?: number; inline?: boolean }) => string;
  /** Just the time for today, otherwise the date and time. */
  formatClock: (iso: string, now?: number) => string;
  formatNumber: (n: number) => string;
}

const I18nContext = createContext<I18n | null>(null);

function readStoredLang(): Lang {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "ar") return stored;
  } catch {
    // Storage can be blocked. The language then resets on reload.
  }
  return "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(readStoredLang);
  const dir = lang === "ar" ? "rtl" : "ltr";
  const locale = LOCALES[lang];

  // The root carries lang and dir. dir="rtl" is what flips the layout and swaps the fonts.
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = dir;
  }, [lang, dir]);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // See readStoredLang.
    }
  }, []);

  const value = useMemo<I18n>(() => {
    const dictionary = dictionaries[lang];
    const t = (key: TKey, params?: Params) => interpolate(dictionary[key] ?? en[key] ?? key, params);

    const rules = new Intl.PluralRules(locale);
    const tn = (key: PluralKey, n: number, params?: Params) => {
      const category = rules.select(n);
      const template = dictionary[`${key}.${category}`] ?? dictionary[`${key}.other`] ?? en[`${key}.other` as TKey] ?? key;
      return interpolate(template, { n, ...params });
    };

    const time = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hour12: false });
    const date = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" });
    const dateLong = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", year: "numeric" });
    const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" });
    const numbers = new Intl.NumberFormat(locale);

    const formatTime = (iso: string) => time.format(new Date(iso));
    // Newer ICU writes "Sept" for en-GB. The design says "Sep".
    const shortMonth = (text: string) => (lang === "en" ? text.replace("Sept", "Sep") : text);
    const formatDate = (iso: string) => shortMonth(dateLong.format(new Date(iso)));
    const formatDateTime = (iso: string) => `${shortMonth(date.format(new Date(iso)))}, ${formatTime(iso)}`;
    const lowerFirst = (text: string) => (lang === "en" ? text.charAt(0).toLowerCase() + text.slice(1) : text);
    const formatWhen = (iso: string, options: { now?: number; inline?: boolean } = {}) => {
      const now = options.now ?? Date.now();
      const then = new Date(iso);
      const minutes = Math.round((now - then.getTime()) / 60000);
      const startOfToday = new Date(now);
      startOfToday.setHours(0, 0, 0, 0);
      let text: string;
      if (minutes < 60 && minutes >= 0) text = relative.format(-minutes, "minute");
      else if (then.getTime() >= startOfToday.getTime()) text = t("when.today", { time: formatTime(iso) });
      else if (then.getTime() >= startOfToday.getTime() - 86400000) text = t("when.yesterday", { time: formatTime(iso) });
      else text = formatDateTime(iso);
      return options.inline ? lowerFirst(text) : text;
    };
    const formatClock = (iso: string, now: number = Date.now()) => {
      const startOfToday = new Date(now);
      startOfToday.setHours(0, 0, 0, 0);
      return Date.parse(iso) >= startOfToday.getTime() ? formatTime(iso) : formatDateTime(iso);
    };

    return {
      lang,
      dir,
      locale,
      setLang,
      t,
      tn,
      formatTime,
      formatDate,
      formatDateTime,
      formatWhen,
      formatClock,
      formatNumber: (n: number) => numbers.format(n),
    };
  }, [lang, dir, locale, setLang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside <I18nProvider>.");
  return value;
}

export type { TKey };
