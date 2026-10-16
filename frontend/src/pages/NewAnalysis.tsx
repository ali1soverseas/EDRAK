import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { RequestForm } from "../components/RequestForm";
import { PageChrome } from "../components/shell/AppShell";
import { Toggle } from "../components/ui/Controls";
import { Icon } from "../components/ui/Icon";
import { WorkerBadge } from "../components/ui/WorkerBadge";
import { useI18n, type TKey } from "../i18n";
import { USE_CASES, defaultForm, enabledSources, FOCUS_BY_USE_CASE } from "../lib/form";
import { useAsync } from "../lib/hooks";
import { api, ApiError } from "../services/api";
import { edrak, type Profile } from "../services/edrak";
import type { AnalysisForm, SourceToggle } from "../types/app";
import type { UseCase, WorkerType } from "../types/contracts";

const SOURCE_ROWS: Array<{ source: SourceToggle; worker: WorkerType; label: TKey }> = [
  { source: "internal_files", worker: "internal_intelligence", label: "form.source.internal" },
  { source: "competitor_web", worker: "competitor_intelligence", label: "form.source.competitor" },
  { source: "news_open_data", worker: "market_intelligence", label: "form.source.news" },
  { source: "reviews_social", worker: "customer_trends", label: "form.source.reviews" },
];

const GOAL_DEFAULT: Record<UseCase, TKey> = {
  competitive_intelligence: "form.goal.default.ci",
  market_entry_expansion: "form.goal.default.me",
  product_launch: "form.goal.default.pl",
};

/** What the company profile already says for a use case: competitors, markets, offering, customers. */
export function prefillFor(useCase: UseCase, p: Profile | null | undefined): Partial<AnalysisForm> {
  if (!p) return {};
  const serves = p.markets.filter((m) => m.relation === "serves").map((m) => m.name);
  const considers = p.markets.filter((m) => m.relation === "considers").map((m) => m.name);
  const markets = useCase === "market_entry_expansion" ? (considers.length ? considers : serves) : serves;
  return {
    competitors: p.competitors.map((c) => c.name),
    market: markets.join(", "),
    offering: useCase === "product_launch" ? "" : (p.offerings[0] ?? ""),
    target_customers: (p.target_customer_groups ?? []).join(", ") || null,
  };
}

function asUseCase(value: string | null): UseCase | null {
  return USE_CASES.find((useCase) => useCase === value) ?? null;
}

export function NewAnalysis() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const draftId = params.get("draft");
  const company = useAsync(() => api.getCompany(), []);
  const draft = useAsync(() => (draftId ? api.getAnalysis(draftId) : Promise.resolve(null)), [draftId]);
  const profile = useAsync(() => edrak.onboarding().then((o) => o.profile).catch(() => null), []);
  // The values the profile filled in, per field, so a use-case switch only replaces what the person has not changed.
  const filled = useRef<Partial<AnalysisForm>>({});

  const [form, setForm] = useState<AnalysisForm | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  // The text the question was last filled in with by the app, so a switch can replace it.
  const suggested = useRef("");

  const companyName = company.data?.name.trim() || t("form.companyFallback");

  // Build the form once the company, and the draft if there is one, have loaded.
  useEffect(() => {
    if (form || company.loading || draft.loading || profile.loading) return;
    if (draft.data) {
      setForm(draft.data.form);
      setReason(draft.data.rejection_reason);
      return;
    }
    const useCase = asUseCase(params.get("use_case")) ?? "competitive_intelligence";
    const typed = params.get("goal");
    const text = typed ?? t(GOAL_DEFAULT[useCase], { company: companyName });
    suggested.current = typed ? "" : text;
    filled.current = prefillFor(useCase, profile.data);
    setForm({ ...defaultForm(useCase), ...filled.current, goal: text });
  }, [form, company.loading, draft.loading, draft.data, profile.loading, profile.data, params, t, companyName]);

  if (company.error || draft.error) {
    return (
      <>
        <PageChrome crumbs={[{ label: t("nav.analyses"), to: "/analyses" }, { label: t("nav.newAnalysis") }]} title={t("nav.newAnalysis")} />
        <div className="form-error" role="alert">
          <Icon name="warning" size={16} />
          <span>{t("common.error.load")}</span>
          <button type="button" className="btn sm" onClick={() => { company.reload(); draft.reload(); }}>
            {t("common.retry")}
          </button>
        </div>
      </>
    );
  }

  const chrome = <PageChrome crumbs={[{ label: t("nav.analyses"), to: "/analyses" }, { label: t("nav.newAnalysis") }]} title={t("nav.newAnalysis")} />;

  if (!form) {
    return (
      <>
        {chrome}
        <div className="analysis-grid" aria-busy="true">
          <div className="col g16 grow">
            <div className="shim shim-title" />
            <div className="shim" style={{ height: 120 }} />
            <div className="shim" style={{ height: 320 }} />
          </div>
          <div className="shim shim-side" />
        </div>
      </>
    );
  }

  const patch = (change: Partial<AnalysisForm>) => setForm((current) => (current ? { ...current, ...change } : current));

  const switchUseCase = (useCase: UseCase) => {
    if (useCase === form.use_case) return;
    const keep = new Set<string>(FOCUS_BY_USE_CASE[useCase]);
    // Replace the question only if the person has not written their own.
    const untouched = form.goal.trim() === "" || form.goal === suggested.current;
    const text = untouched ? t(GOAL_DEFAULT[useCase], { company: companyName }) : form.goal;
    if (untouched) suggested.current = text;
    const next = prefillFor(useCase, profile.data);
    const swap: Partial<AnalysisForm> = {};
    (Object.keys(next) as (keyof AnalysisForm)[]).forEach((k) => {
      if (JSON.stringify(form[k]) === JSON.stringify(filled.current[k])) (swap as Record<string, unknown>)[k] = next[k];
    });
    filled.current = { ...filled.current, ...next };
    patch({ use_case: useCase, goal: text, focus: form.focus.filter((key) => keep.has(key)), ...swap });
  };

  const sourcesOn = enabledSources(form).length;
  const goalMissing = form.goal.trim().length === 0;
  const indexed = company.data?.documents.filter((doc) => doc.status === "indexed").length ?? 0;

  const submit = async () => {
    setAttempted(true);
    setError(null);
    if (goalMissing || sourcesOn === 0) return;
    setBusy(true);
    try {
      const detail = await api.draftPlan(form, draftId ?? undefined);
      navigate(`/analyses/${detail.analysis_id}/plan`);
    } catch (err) {
      setError(err instanceof ApiError ? { code: err.code, message: err.message } : { code: "wrong_state", message: "" });
      setBusy(false);
    }
  };

  /** Guardrail block codes the backend can return on draft; shown verbatim. */
  const GUARDRAIL_BLOCKS = new Set([
    "prompt_injection",
    "prompt_injection_semantic",
    "harmful_request",
    "harmful_request_semantic",
    "off_topic",
    "query_too_short",
    "query_too_long",
  ]);

  const steps: TKey[] = ["form.next.1", "form.next.2", "form.next.3"];

  return (
    <>
      {chrome}
      <div className="analysis-grid">
        <div className="col grow" style={{ minWidth: 0, gap: 18 }}>
          <div>
            <div className="eyebrow">{t("nav.newAnalysis")}</div>
            <h1 className="disp page-h1">
              {t("form.title")}
            </h1>
          </div>

          {reason && (
            <div className="card sunk row as g10" style={{ padding: "14px 16px" }} role="note">
              <Icon name="info" size={16} className="t2" />
              <div className="s13 t2 lh14">
                <b style={{ color: "var(--ink)" }}>{t("form.sentBack")}</b> {t("form.sentBackReason", { reason })}
              </div>
            </div>
          )}

          <RequestForm form={form} onChange={patch} onUseCaseChange={switchUseCase} showGoalError={attempted && goalMissing}
            fromProfile={(["competitors", "market", "offering", "target_customers"] as const).filter((k) => {
              const v = form[k];
              return (Array.isArray(v) ? v.length > 0 : !!v) && JSON.stringify(v) === JSON.stringify(filled.current[k]);
            })} />
        </div>

        <aside className="col analysis-side">
          <div className="card col" style={{ padding: 22, gap: 16, flex: 1 }}>
            <div className="eyebrow">{t("form.where")}</div>
            <div className="col" style={{ gap: 14 }}>
              {SOURCE_ROWS.map((row) => (
                <div key={row.source} className="row g10">
                  <WorkerBadge worker={row.worker} iconSize={15} />
                  <div className="grow">
                    <div className="s13">{t(row.label)}</div>
                    {row.source === "internal_files" && <div className="s12 t3">{t("form.source.internalCount", { n: indexed })}</div>}
                  </div>
                  <Toggle
                    label={t(row.label)}
                    on={form.sources[row.source]}
                    onChange={(next) => patch({ sources: { ...form.sources, [row.source]: next } })}
                  />
                </div>
              ))}
              {attempted && sourcesOn === 0 && <div className="field-error">{t("form.sourcesRequired")}</div>}
            </div>
            <hr className="rule" />
            <div className="col" style={{ gap: 0 }}>
              <div className="eyebrow" style={{ marginBottom: 6 }}>
                {t("form.next")}
              </div>
              {steps.map((key, index) => (
                <div key={key} className="row as g10" style={{ padding: "8px 0", borderTop: "1px solid var(--hair)" }}>
                  <span className="mono tb" style={{ width: 20 }}>
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="s13 t2 lh14">{t(key)}</span>
                </div>
              ))}
            </div>
            <div className="grow" />
            <div className="col g8">
              {error && (
                <div className="form-error" role="alert">
                  <Icon name="warning" size={16} />
                  <span>{error.message || t("form.draftError")}</span>
                  {GUARDRAIL_BLOCKS.has(error.code) && (
                    <span className="mono" style={{ direction: "ltr", unicodeBidi: "embed" }}> {error.code}</span>
                  )}
                </div>
              )}
              <button type="button" className="btn pri lg" style={{ width: "100%" }} disabled={busy} aria-busy={busy} onClick={() => void submit()}>
                {t("form.submit")}
                <Icon name="arrow" size={18} flip />
              </button>
              <div className="s12 t3" style={{ textAlign: "center" }}>
                {t("form.submitHint")}
              </div>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}

