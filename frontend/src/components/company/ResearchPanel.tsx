import { useState } from "react";
import { Link } from "react-router-dom";
import { useAsync } from "../../lib/hooks";
import {
  STAGES, activeRunId, durationLabel, hostOf, isActive, researchErrorMessage, runSeconds, runSummary, stageProgress,
} from "../../lib/research";
import { useLatestRun } from "../../lib/useResearch";
import { useL } from "../../lib/useL";
import { edrak, type ResearchRun } from "../../services/edrak";
import { Icon } from "../ui/Icon";
import { ProgressBar } from "../ui/Progress";
import { ThinkingMark } from "../ui/ThinkingMark";
import { ResearchFacts } from "./ResearchFacts";

interface StartProps {
  saved: string;
  busy: boolean;
  error: string | null;
  again: boolean;
  onStart: (url: string) => void;
}

/** Ask for the website (prefilled with the saved one) and start. */
function StartCard({ saved, busy, error, again, onStart }: StartProps) {
  const L = useL();
  const [url, setUrl] = useState(saved);
  const typed = url.trim();
  return (
    <div className="research-start card pad stack gap12">
      <div className="row g12" style={{ alignItems: "flex-start" }}>
        <span className="import-mark"><Icon name="sparkle" size={15} /></span>
        <div className="stack gap4 grow">
          <b>{again ? L("Research your company again", "ابحث عن شركتك مجددًا") : L("Find out more about your company", "اعرف المزيد عن شركتك")}</b>
          <span className="s13 t2">
            {L("Edrak reads your website and the PDFs it publishes, and lists what it finds by topic: leadership, ownership, financials, locations and more. Each fact comes with the exact words and the page it came from. Nothing is used until you approve it.",
              "يقرأ إدراك موقعك وملفات PDF التي ينشرها، ويعرض ما يجده حسب الموضوع: القيادة والملكية والبيانات المالية والمواقع وغيرها. تأتي كل حقيقة مع النص الدقيق والصفحة التي أُخذت منها. لا يُستخدم شيء قبل أن تعتمده.")}
          </span>
        </div>
      </div>
      <div className="row g8 import-url">
        <input className="input" dir="ltr" inputMode="url" autoComplete="url" placeholder="example.com" value={url} disabled={busy}
          aria-label={L("Company website", "موقع الشركة")} onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && typed && !busy) { e.preventDefault(); onStart(typed); } }} />
        <button type="button" className="btn pri" disabled={busy || !typed} onClick={() => onStart(typed)}>
          {busy ? L("Starting…", "جارٍ البدء…") : L("Research this company", "ابحث عن هذه الشركة")} <Icon name="arrow" size={15} flip />
        </button>
      </div>
      <span className="s12 t3">
        {saved
          ? L("This takes about 5 minutes. You can leave this page; the research keeps going.", "يستغرق هذا نحو 5 دقائق. يمكنك مغادرة الصفحة وسيستمر البحث.")
          : L("The address is saved to your company profile. This takes about 5 minutes, and you can leave this page while it runs.", "يُحفظ العنوان في ملف شركتك. يستغرق هذا نحو 5 دقائق ويمكنك مغادرة الصفحة أثناء العمل.")}
      </span>
      {error && <div className="form-error" role="alert"><Icon name="warning" size={16} /><span className="grow">{error}</span></div>}
    </div>
  );
}

function Progress({ run, onCancel, cancelling }: { run: ResearchRun; onCancel: () => void; cancelling: boolean }) {
  const L = useL();
  const at = STAGES.findIndex(([key]) => key === run.stage);
  return (
    <div className="research-progress card pad stack gap12" role="status" aria-live="polite">
      <div className="row g12">
        <ThinkingMark size={30} color="var(--blue)" />
        <div className="stack gap4 grow">
          <b>{L("Researching your company", "جارٍ البحث عن شركتك")}</b>
          <span className="s13 t2">{runSummary(run, L)}</span>
        </div>
        <button type="button" className="btn ghost sm" disabled={cancelling} onClick={onCancel}>{L("Stop", "إيقاف")}</button>
      </div>
      <ProgressBar value={stageProgress(run)} color="var(--blue)" />
      <ol className="research-steps">
        {STAGES.filter(([key]) => !["own_social", "third_party"].includes(key) || run.stage === key).map(([key, en, ar]) => {
          const index = STAGES.findIndex(([k]) => k === key);
          const state = index < at ? "done" : index === at ? "now" : "next";
          return <li key={key} className={state}>{state === "done" ? <Icon name="check" size={12} stroke={2.4} /> : <i />} {L(en, ar)}</li>;
        })}
      </ol>
      <span className="s12 t3">{L("You can keep using Edrak. A notice at the top shows when the facts are ready.", "يمكنك متابعة استخدام إدراك. سيظهر إشعار في الأعلى عند جاهزية الحقائق.")}</span>
    </div>
  );
}

function Result({ run }: { run: ResearchRun }) {
  const L = useL();
  const seconds = runSeconds(run);
  const failed = run.status === "failed";
  return (
    <div className={`research-result card pad stack gap8 ${failed ? "bad" : ""}`.trim()}>
      <div className="row g10 wrap">
        {run.status === "done" && <span className="chip c-ok"><Icon name="check" size={12} /> {L("Research finished", "اكتمل البحث")}</span>}
        {failed && <span className="chip c-bad"><Icon name="x" size={12} /> {L("Research failed", "فشل البحث")}</span>}
        {run.status === "cancelled" && <span className="chip c-draft">{L("Stopped", "تم الإيقاف")}</span>}
        <span className="s13 t2">{runSummary(run, L)}{seconds !== null && run.status === "done" ? ` · ${durationLabel(seconds, L)}` : ""}</span>
      </div>
      {failed && run.error && <span className="s13" dir="auto">{run.error}</span>}
      {run.warnings.length > 0 && (
        <details className="research-warn">
          <summary>{L(`${run.warnings.length} notes from this run`, `${run.warnings.length} ملاحظة من هذه المحاولة`)}</summary>
          <ul>{run.warnings.map((w) => <li key={w} dir="auto">{w}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

interface PanelProps {
  /** "compact" is the version inside company setup: no heading, and the list scrolls. */
  variant?: "page" | "compact";
}

/**
 * Company research in one place: start it (typing the website if none is saved), follow it while it runs, and review the
 * facts it found. The run goes on in the background, so the person can leave and come back.
 */
export function ResearchPanel({ variant = "page" }: PanelProps) {
  const L = useL();
  const { run, loading, reload } = useLatestRun();
  const profile = useAsync(() => edrak.onboarding(), []);
  const saved = profile.data?.profile.website ?? "";
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showStart, setShowStart] = useState(false);

  const start = async (url: string) => {
    setStarting(true);
    setError(null);
    try {
      if (!saved) await edrak.saveOnboarding({ website: url }); // a first address becomes the company's website
      await edrak.startResearch(url);
      setShowStart(false);
    } catch (e) {
      if (activeRunId(e) === null) setError(researchErrorMessage(e, L)); // already running: just show that run
      else setShowStart(false);
    } finally {
      setStarting(false);
      reload();
      profile.reload();
    }
  };
  const cancel = async () => {
    if (!run) return;
    setCancelling(true);
    try { await edrak.cancelResearch(run.id); } catch { /* it may have just finished */ } finally { setCancelling(false); reload(); }
  };

  if (loading || !profile.data) return <div className="shim" style={{ height: 180, borderRadius: 16 }} aria-busy="true" />;

  const active = isActive(run);
  return (
    <div className={`research research-${variant}`}>
      {active && run ? <Progress run={run} onCancel={() => void cancel()} cancelling={cancelling} />
        : !run || showStart ? <StartCard saved={saved} busy={starting} error={error} again={!!run} onStart={(u) => void start(u)} />
        : (
          <div className="stack gap8">
            <Result run={run} />
            <div className="row g8">
              <button type="button" className="btn sm" onClick={() => setShowStart(true)}><Icon name="refresh" size={14} /> {L("Run again", "شغّل مجددًا")}</button>
              {saved && <span className="s12 t3">{hostOf(run.input.seed_url ?? saved)}</span>}
            </div>
          </div>
        )}
      <div className="research-head row g8">
        <b className="research-title">{L("What Edrak found", "ما وجده إدراك")}</b>
        {variant === "compact" && <Link className="s13" to="/company/research">{L("Open full view", "افتح العرض الكامل")}</Link>}
      </div>
      <ResearchFacts refreshKey={`${run?.id ?? "none"}:${run?.status ?? ""}`} active={active} />
    </div>
  );
}

