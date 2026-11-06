import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "../ui/Icon";
import { ProgressBar } from "../ui/Progress";
import { CompetitorProfileView } from "./CompetitorProfileView";
import { useL } from "../../lib/useL";
import { edrak, type CompetitorKb, type KbStatus } from "../../services/edrak";

const POLL_MS = 2000;
const ACTIVE: ReadonlySet<KbStatus> = new Set(["queued", "building"]);

const STAGE: Record<string, [string, string]> = {
  queued: ["Waiting to start", "في الانتظار"],
  identifying: ["Finding the official website", "جارٍ إيجاد الموقع الرسمي"],
  crawling: ["Reading the website", "جارٍ قراءة الموقع"],
  searching: ["Searching public sources", "جارٍ البحث في المصادر العامة"],
  profiling: ["Building the profile", "جارٍ بناء الملف"],
  indexing: ["Saving to the knowledge base", "جارٍ الحفظ في قاعدة المعرفة"],
};

export const isBuilding = (rows: CompetitorKb[]) => rows.some((r) => ACTIVE.has(r.status));

interface Props {
  /** Queue builds first (POST /competitor-kb/build). Leave false when the save already queued them. */
  start?: boolean;
  /** Called once, when no competitor is queued or building any more. */
  onDone?: (rows: CompetitorKb[]) => void;
  /** Shows a "Continue while this runs" button. */
  onBackground?: () => void;
  /** A smaller list for the company profile page. */
  compact?: boolean;
  /** Each finished competitor gets "View profile", which opens its profile and results inline. */
  showProfiles?: boolean;
  /** Shows a Continue button once every build has finished (instead of leaving automatically). */
  onContinue?: () => void;
}

/** "Loading competitors…": polls the knowledge-base builds and shows each competitor's progress. */
export function CompetitorKbProgress({ start = false, onDone, onBackground, compact = false, showProfiles = false, onContinue }: Props) {
  const L = useL();
  const [openId, setOpenId] = useState<string | null>(null);
  const [rows, setRows] = useState<CompetitorKb[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const doneRef = useRef(false);
  const alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;
  const showProfilesRef = useRef(showProfiles);
  showProfilesRef.current = showProfiles;

  /** One status read; schedules the next while anything is still building. */
  const poll = useCallback(async () => {
    clearTimeout(timer.current);
    try {
      const next = await edrak.competitorKbStatus();
      if (!alive.current) return;
      setRows(next);
      setError(null);
      if (isBuilding(next)) {
        timer.current = setTimeout(() => void poll(), POLL_MS);
      } else if (!doneRef.current) {
        doneRef.current = true;
        if (showProfilesRef.current) {
          const first = next.find((r) => r.status === "ready" || r.status === "partial");
          if (first) setOpenId((cur) => cur ?? first.entity_id);
        }
        onDoneRef.current?.(next);
      }
    } catch (e) {
      if (!alive.current) return;
      setError((e as Error).message);
      timer.current = setTimeout(() => void poll(), POLL_MS * 2); // the server may be restarting
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void (async () => {
      if (start) {
        try {
          setRows(await edrak.buildCompetitorKb());
        } catch (e) {
          setError((e as Error).message);
        }
      }
      await poll();
    })();
    return () => {
      alive.current = false;
      clearTimeout(timer.current);
    };
  }, [start, poll]);

  const retry = async (entityId: string) => {
    doneRef.current = false;
    try {
      await edrak.buildCompetitorKb([entityId], true);
    } catch (e) {
      setError((e as Error).message);
    }
    await poll();
  };

  if (rows === null) {
    return (
      <div className="panel row g10 kb-progress" aria-busy="true">
        <span className="kb-spin" aria-hidden="true" />
        <b>{L("Loading competitors…", "جارٍ تحميل المنافسين…")}</b>
      </div>
    );
  }
  if (rows.length === 0) return null;

  const busy = isBuilding(rows);
  const finished = rows.filter((r) => !ACTIVE.has(r.status)).length;

  return (
    <div className={`panel stack gap12 kb-progress ${compact ? "compact" : ""}`} aria-live="polite" aria-busy={busy}>
      <div className="row g10 items-center">
        {busy ? <span className="kb-spin" aria-hidden="true" /> : <Icon name="check" size={16} />}
        <b>{busy ? L("Loading competitors…", "جارٍ تحميل المنافسين…") : L("Competitors ready", "المنافسون جاهزون")}</b>
        <span className="s12 muted">{finished}/{rows.length}</span>
      </div>
      {!compact && busy && (
        <span className="s13 muted">
          {L(
            "Edrak is reading each competitor's website and public sources and building its knowledge base. This takes a minute or two per competitor.",
            "يقرأ إدراك موقع كل منافس ومصادره العامة ويبني قاعدة معرفته. يستغرق ذلك دقيقة أو دقيقتين لكل منافس.",
          )}
        </span>
      )}

      <div className="stack gap8">
        {rows.map((r) => (
          <div key={r.entity_id} className="kb-row">
            <div className="stack gap4" style={{ minWidth: 0 }}>
              <bdi className="b6">{r.name}</bdi>
              {r.website_domain && <span className="s12 muted" dir="ltr">{r.website_domain}</span>}
            </div>
            <div className="stack gap4 grow" style={{ minWidth: 0 }}>
              {ACTIVE.has(r.status) ? (
                <>
                  <span className="s12">{L(...(STAGE[r.stage ?? "queued"] ?? STAGE.queued))} · {r.progress}%</span>
                  <ProgressBar value={r.progress} color="var(--blue)" />
                </>
              ) : r.status === "ready" ? (
                <span className="chip c-ok">
                  <Icon name="check" size={12} /> {L("Ready", "جاهز")} · {r.page_count} {L("pages", "صفحات")}
                </span>
              ) : r.status === "partial" ? (
                <span className="chip c-ins" title={L("Few official pages could be read.", "تعذرت قراءة صفحات رسمية كافية.")}>
                  {L("Ready with gaps", "جاهز مع نواقص")} · {r.page_count} {L("pages", "صفحات")}
                </span>
              ) : r.status === "failed" ? (
                <span className="chip c-bad" title={r.error_message ?? ""}>{L("Could not build", "تعذر البناء")}</span>
              ) : (
                <span className="s12 muted">{L("Not built yet", "لم يُبنَ بعد")}</span>
              )}
            </div>
            <div className="row g6">
              {showProfiles && (r.status === "ready" || r.status === "partial") && (
                <button type="button" className={`btn sm ${openId === r.entity_id ? "ink" : "ghost"}`} aria-expanded={openId === r.entity_id}
                  onClick={() => setOpenId(openId === r.entity_id ? null : r.entity_id)}>
                  {openId === r.entity_id ? L("Hide profile", "أخفِ الملف") : L("View profile", "اعرض الملف")}
                </button>
              )}
              {(r.status === "failed" || r.status === "missing" || (compact && !ACTIVE.has(r.status))) && (
                <button type="button" className="btn ghost sm" onClick={() => void retry(r.entity_id)}>
                  {r.status === "failed" || r.status === "missing" ? L("Retry", "أعد المحاولة") : L("Refresh", "حدّث")}
                </button>
              )}
            </div>
            {showProfiles && openId === r.entity_id && (
              <div className="kb-inline">
                <CompetitorProfileView entityId={r.entity_id} compact />
              </div>
            )}
          </div>
        ))}
      </div>

      {error && <span className="s12" style={{ color: "var(--red, #c0392b)" }}>{error}</span>}
      {!busy && onContinue && (
        <div className="row g8">
          <button type="button" className="btn ink" onClick={onContinue}>
            {L("Continue", "متابعة")}
          </button>
        </div>
      )}
      {busy && onBackground && (
        <div className="row g8">
          <button type="button" className="btn ghost" onClick={onBackground}>
            {L("Continue while this runs", "تابع أثناء التحميل")}
          </button>
        </div>
      )}
    </div>
  );
}
