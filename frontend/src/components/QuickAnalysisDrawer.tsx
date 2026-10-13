import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n } from "../i18n";
import { defaultForm } from "../lib/form";
import { useL } from "../lib/useL";
import { prefillFor } from "../pages/NewAnalysis";
import { api } from "../services/api";
import { edrak, type Dict, type Profile } from "../services/edrak";
import type { AnalysisForm } from "../types/app";
import type { UseCase } from "../types/contracts";
import { Icon } from "./ui/Icon";
import { Overlay } from "./ui/Overlay";

const QUESTION: Record<UseCase, (name: string, company: string) => [string, string]> = {
  competitive_intelligence: (n, c) => [`What is ${n} doing, and what does it mean for ${c}?`, `ماذا تفعل ${n}، وماذا يعني ذلك لـ${c}؟`],
  market_entry_expansion: (n, c) => [`Should ${c} enter ${n}, and what would it take?`, `هل يجب أن تدخل ${c} ${n}، وما المطلوب؟`],
  product_launch: (n, c) => [`How would a new ${c} offering compete with ${n}?`, `كيف سينافس منتج جديد من ${c} ${n}؟`],
};

/** Start an analysis about one watched competitor or market, without leaving the watchlist. */
export function QuickAnalysisDrawer({ entity, onClose }: { entity: Dict; onClose: () => void }) {
  const L = useL();
  const { t } = useI18n();
  const navigate = useNavigate();
  const isMarket = entity.kind === "market";
  const [profile, setProfile] = useState<Profile | null>(null);
  const [useCase, setUseCase] = useState<UseCase>(isMarket ? "market_entry_expansion" : "competitive_intelligence");
  const [goal, setGoal] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { void edrak.onboarding().then((o) => setProfile(o.profile)).catch(() => setProfile(null)); }, []);
  const company = profile?.name || L("us", "نحن");
  useEffect(() => {
    if (!touched) setGoal(L(...QUESTION[useCase](entity.name, company)));
  }, [useCase, company, entity.name, touched, L]);

  const start = async () => {
    setBusy(true); setError(null);
    const base = prefillFor(useCase, profile);
    const form: AnalysisForm = {
      ...defaultForm(useCase), ...base, goal: goal.trim(),
      // The analysis is about this entity: it leads the list (competitor) or is the market.
      competitors: isMarket ? (base.competitors ?? []) : [entity.name, ...(base.competitors ?? []).filter((c) => c.toLowerCase() !== String(entity.name).toLowerCase())].slice(0, isMarket ? 8 : 1),
      market: isMarket ? entity.name : (base.market ?? ""),
    };
    try {
      const detail = await api.draftPlan(form);
      navigate(`/analyses/${detail.analysis_id}/plan`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  const cases: [UseCase, string, string][] = [
    ["competitive_intelligence", "Compare", "قارن"], ["market_entry_expansion", "Enter a market", "دخول سوق"], ["product_launch", "Launch against it", "أطلق منافسًا"],
  ];
  return (
    <Overlay variant="drawer" label={L(`New analysis about ${entity.name}`, `تحليل جديد عن ${entity.name}`)} onClose={onClose}>
      {(close) => (
        <div className="drawer-body quick">
          <div className="row g8">
            <div className="stack grow"><span className="eyebrow">{L("New analysis", "تحليل جديد")}</span>
              <h3 className="drawer-title" dir="auto">{L(`About ${entity.name}`, `عن ${entity.name}`)}</h3></div>
            <button type="button" className="iconbtn" data-autofocus onClick={close} aria-label={L("Close", "إغلاق")}><Icon name="x" size={16} /></button>
          </div>
          <div className="stack gap6"><span className="lbl">{L("What do you want to know?", "ماذا تريد أن تعرف؟")}</span>
            <div className="seg" role="radiogroup" aria-label={t("form.useCase")}>
              {cases.map(([key, en, ar]) => <button key={key} type="button" role="radio" aria-checked={useCase === key} className={useCase === key ? "on" : ""} onClick={() => setUseCase(key)}>{L(en, ar)}</button>)}
            </div>
          </div>
          <label className="stack gap6"><span className="lbl">{t("form.goal")}</span>
            <textarea className="inp area serif" rows={3} style={{ fontSize: 17 }} value={goal} dir="auto" onChange={(e) => { setTouched(true); setGoal(e.target.value); }} /></label>
          <div className="quick-scope">
            <Icon name="info" size={14} />
            <span className="s13 t2">{isMarket
              ? L(`The market is ${entity.name}. Competitors, offering and customers come from your company profile.`, `السوق هو ${entity.name}. المنافسون والعرض والعملاء من ملف شركتك.`)
              : L(`Edrak researches ${entity.name} against ${company}. Markets and customers come from your company profile.`, `يبحث إدراك في ${entity.name} مقارنة بـ${company}. الأسواق والعملاء من ملف شركتك.`)}</span>
          </div>
          {error && <div className="chip c-bad wrap-chip">{error}</div>}
          <div className="row g8">
            <button type="button" className="btn ghost" onClick={() => navigate(`/analyses/new?use_case=${useCase}&goal=${encodeURIComponent(goal)}`)}>{L("More options", "خيارات أكثر")}</button>
            <span className="grow" />
            <button type="button" className="btn pri" disabled={busy || goal.trim().length < 8} onClick={() => void start()}>
              {busy ? L("Drafting the plan…", "جارٍ إعداد الخطة…") : <>{L("Draft the plan", "أعدّ الخطة")} <Icon name="arrow" size={14} flip /></>}
            </button>
          </div>
          <span className="s12 t3">{L("Nothing runs until you approve the plan.", "لا يبدأ شيء حتى تعتمد الخطة.")}</span>
        </div>
      )}
    </Overlay>
  );
}
