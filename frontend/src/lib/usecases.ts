import type { UseCase } from "../types/contracts";
import type { IconName } from "../components/ui/Icon";
import type { TKey } from "../i18n/en";

export interface UseCaseMeta {
  icon: IconName;
  /** Full name, for the cards. */
  title: TKey;
  /** Short name, for the list rows and eyebrows. */
  short: TKey;
  description: TKey;
}

export const USE_CASE_META: Record<UseCase, UseCaseMeta> = {
  competitive_intelligence: {
    icon: "activity",
    title: "usecase.ci.title",
    short: "usecase.ci.short",
    description: "usecase.ci.desc",
  },
  market_entry_expansion: {
    icon: "globe",
    title: "usecase.me.title",
    short: "usecase.me.short",
    description: "usecase.me.desc",
  },
  product_launch: {
    icon: "sparkle",
    title: "usecase.pl.title",
    short: "usecase.pl.short",
    description: "usecase.pl.desc",
  },
};
