/**
 * The left side of New analysis: the use-case switch and the questions for that use
 * case. Which fields show, and what they ask, depends on the use case.
 */
import type { UseCase } from "../types/contracts";
import type { AnalysisForm, FocusKey, TargetCustomers } from "../types/app";
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

const CUSTOMER_LABEL: Record<TargetCustomers, TKey> = {
  smes: "form.customers.smes",
  enterprise: "form.customers.enterprise",
  both: "form.customers.both",
};

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
}

export function RequestForm({ form, onChange, onUseCaseChange, showGoalError }: RequestFormProps) {
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
            <>
              <div>
                <label className="lbl" htmlFor="offering">
                  {t("form.offering")}
                </label>
                <input id="offering" className="inp" value={form.offering} onChange={(event) => onChange({ offering: event.target.value })} />
              </div>
              <div className="row g16 as split">
                <div className="grow">
                  <div className="lbl">{t("form.customers")}</div>
                  <ChoiceChips<TargetCustomers>
                    label={t("form.customers")}
                    options={(["smes", "enterprise", "both"] as const).map((value) => ({ value, label: t(CUSTOMER_LABEL[value]) }))}
                    selected={form.target_customers ? [form.target_customers] : []}
                    onToggle={(value) => onChange({ target_customers: form.target_customers === value ? null : value })}
                  />
                </div>
                <div className="grow">
                  <label className="lbl" htmlFor="market">
                    {t("form.market")}
                  </label>
                  <input id="market" className="inp" value={form.market} onChange={(event) => onChange({ market: event.target.value })} />
                </div>
              </div>
              <div>
                <div className="lbl">{t("form.alternatives")}</div>
                <TagInput values={form.competitors} onChange={(values) => onChange({ competitors: values })} label={t("form.alternatives")} />
                <div className="help">{t("form.alternativesHelp")}</div>
              </div>
            </>
          )}

          {useCase === "competitive_intelligence" && (
            <>
              <div>
                <div className="lbl">{t("form.competitors")}</div>
                <TagInput values={form.competitors} onChange={(values) => onChange({ competitors: values })} label={t("form.competitors")} />
                <div className="help">{t("form.alternativesHelp")}</div>
              </div>
              <div>
                <label className="lbl" htmlFor="market">
                  {t("form.marketOptional")}
                </label>
                <input id="market" className="inp" value={form.market} onChange={(event) => onChange({ market: event.target.value })} />
              </div>
              <div className="row g16 as split">
                <div className="grow">{focusChips}</div>
                <div className="grow">{timeWindow}</div>
              </div>
            </>
          )}

          {useCase === "market_entry_expansion" && (
            <>
              <div>
                <label className="lbl" htmlFor="market">
                  {t("form.marketTarget")}
                </label>
                <input id="market" className="inp" value={form.market} onChange={(event) => onChange({ market: event.target.value })} />
              </div>
              {focusChips}
              <div>
                <div className="lbl">{t("form.players")}</div>
                <TagInput values={form.competitors} onChange={(values) => onChange({ competitors: values })} label={t("form.players")} />
                <div className="help">{t("form.alternativesHelp")}</div>
              </div>
              {timeWindow}
            </>
          )}
        </div>
      </section>
    </>
  );
}
