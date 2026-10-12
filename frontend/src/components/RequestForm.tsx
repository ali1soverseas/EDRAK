/**
 * The left side of New analysis: the use-case switch and the questions for that use
 * case. Which fields show, and what they ask, depends on the use case.
 */
import type { UseCase } from "../types/contracts";
import type { AnalysisForm, FocusKey } from "../types/app";
import { useI18n, type TKey } from "../i18n";
import { FOCUS_BY_USE_CASE, TIME_WINDOWS, USE_CASES } from "../lib/form";
import { USE_CASE_META } from "../lib/usecases";
import { ChoiceChips, TagInput } from "./ui/Controls";
import { Icon } from "./ui/Icon";

const FOCUS_LABEL: Record<FocusKey, TKey> = {
  features: "form.focus.features",
  pricing: "form.focus.pricing",
  positioning: "form.focus.positioning",
  sentiment: "form.focus.sentiment",
  demand: "form.focus.demand",
  regulation: "form.focus.regulation",
  competition: "form.focus.competition",
  customers: "form.focus.customers",
};

const WINDOW_LABEL: Record<number, TKey> = {
  30: "form.window.30",
  90: "form.window.90",
  365: "form.window.365",
};

const HELP: Record<UseCase, { competitors: TKey; markets: TKey }> = {
  competitive_intelligence: { competitors: "form.competitorsHelp.ci", markets: "form.marketsHelp.ci" },
  market_entry_expansion: { competitors: "form.competitorsHelp.me", markets: "form.marketsHelp.me" },
  product_launch: { competitors: "form.competitorsHelp.pl", markets: "form.marketsHelp.pl" },
};

const split = (text: string | null | undefined) => (text ?? "").split(",").map((x) => x.trim()).filter(Boolean);

interface UseCaseSwitchProps {
  value: UseCase;
  onChange: (useCase: UseCase) => void;
}

function UseCaseSwitch({ value, onChange }: UseCaseSwitchProps) {
  const { t } = useI18n();
  return (
    <div className="row uc-switch" role="radiogroup" aria-label={t("form.useCase")}>
      {USE_CASES.map((useCase) => {
        const meta = USE_CASE_META[useCase];
        const on = useCase === value;
        return (
          <button
            key={useCase}
            type="button"
            role="radio"
            aria-checked={on}
            className={`uc-option ${on ? "on" : ""}`.trim()}
            onClick={() => onChange(useCase)}
          >
            <span className="row g10" style={{ width: "100%" }}>
              <span className="uc-option-icon">
                <Icon name={meta.icon} size={18} />
              </span>
              <span className="grow" />
              <span className="uc-option-radio" />
            </span>
            <span className="b7 s15 uc-title" style={{ textAlign: "start" }}>
              {t(meta.title)}
            </span>
            <span className="t2 s13 lh14" style={{ textAlign: "start", fontWeight: 400 }}>
              {t(meta.description)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

interface RequestFormProps {
  form: AnalysisForm;
  onChange: (patch: Partial<AnalysisForm>) => void;
  onUseCaseChange: (useCase: UseCase) => void;
  /** Show the "write a question" error. */
  showGoalError: boolean;
  /** Fields still holding what the company profile filled in. */
  fromProfile?: ReadonlyArray<"competitors" | "market" | "offering" | "target_customers">;
}

export function RequestForm({ form, onChange, onUseCaseChange, showGoalError, fromProfile = [] }: RequestFormProps) {
  const { t } = useI18n();
  const useCase = form.use_case;

  const toggleFocus = (key: FocusKey) =>
    onChange({ focus: form.focus.includes(key) ? form.focus.filter((value) => value !== key) : [...form.focus, key] });

  const timeWindow = (
    <div>
      <div className="lbl">{t("form.window")}</div>
      <ChoiceChips
        label={t("form.window")}
        options={TIME_WINDOWS.map((days) => ({ value: String(days), label: t(WINDOW_LABEL[days]) }))}
        selected={form.time_window_days ? [String(form.time_window_days)] : []}
        onToggle={(value) => onChange({ time_window_days: form.time_window_days === Number(value) ? null : Number(value) })}
      />
    </div>
  );

  const focusChips = (
    <div>
      <div className="lbl">{t(useCase === "market_entry_expansion" ? "form.focus.me" : "form.focus.ci")}</div>
      <ChoiceChips
        label={t("form.focus.ci")}
        options={FOCUS_BY_USE_CASE[useCase].map((key) => ({ value: key, label: t(FOCUS_LABEL[key]) }))}
        selected={form.focus}
        onToggle={toggleFocus}
      />
    </div>
  );

  return (
    <>
      <UseCaseSwitch value={useCase} onChange={onUseCaseChange} />

      <section className="card col" style={{ padding: "22px 24px", gap: 18, flex: 1 }}>
        <div className="row jb">
          <span className="eyebrow">{t("form.details")}</span>
          <span className="s12 t3">{t("form.detailsHint")}</span>
        </div>

        <div className="col" style={{ gap: 18 }}>
          <div>
            <label className="lbl" htmlFor="goal">
              {t("form.goal")}
            </label>
            <textarea
              id="goal"
              className="inp serif"
              rows={2}
              style={{ fontSize: 19, lineHeight: 1.4 }}
              value={form.goal}
              aria-invalid={showGoalError || undefined}
              aria-describedby={showGoalError ? "goal-error" : undefined}
              onChange={(event) => onChange({ goal: event.target.value })}
            />
            {showGoalError && (
              <div id="goal-error" className="field-error">
                {t("form.goalRequired")}
              </div>
            )}
          </div>

          {useCase === "product_launch" && (
            <div>
              <label className="lbl row g8" htmlFor="offering">{t("form.offering")}</label>
              <input id="offering" className="inp" value={form.offering} onChange={(event) => onChange({ offering: event.target.value })} dir="auto" />
            </div>
          )}

          <div>
            <div className="lbl row g8">{t("form.competitors")}{fromProfile.includes("competitors") && <span className="chip c-blue from-profile">{t("form.fromProfile")}</span>}</div>
            <TagInput values={form.competitors} onChange={(values) => onChange({ competitors: values })} label={t("form.competitors")} addLabel={t("form.addCompetitor")} />
            <div className="help">{t(HELP[useCase].competitors)}</div>
          </div>

          <div className="row g16 as split">
            <div className="grow">
              <div className="lbl row g8">{t("form.markets")}{fromProfile.includes("market") && <span className="chip c-blue from-profile">{t("form.fromProfile")}</span>}</div>
              <TagInput values={split(form.market)} onChange={(values) => onChange({ market: values.join(", ") })} label={t("form.markets")} addLabel={t("form.addMarket")} />
              <div className="help">{t(HELP[useCase].markets)}</div>
            </div>
            <div className="grow">
              <div className="lbl row g8">{t("form.customers")}{fromProfile.includes("target_customers") && <span className="chip c-blue from-profile">{t("form.fromProfile")}</span>}</div>
              <TagInput values={split(form.target_customers)} onChange={(values) => onChange({ target_customers: values.join(", ") || null })} label={t("form.customers")} addLabel={t("form.addCustomer")} />
              <div className="help">{t("form.customersHelp")}</div>
            </div>
          </div>

          {useCase !== "product_launch" && (
            <div className="row g16 as split">
              <div className="grow">{focusChips}</div>
              <div className="grow">{timeWindow}</div>
            </div>
          )}
        </div>
      </section>
    </>
  );
}
