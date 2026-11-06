import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CompetitorKbProgress, isBuilding } from "../components/competitors/CompetitorKbProgress";
import { CompetitorProfileView } from "../components/competitors/CompetitorProfileView";
import { PageChrome } from "../components/shell/AppShell";
import { useL } from "../lib/useL";
import { edrak, type CompetitorKb } from "../services/edrak";

const READY = new Set(["ready", "partial"]);

/** Every tracked competitor's knowledge base: pick one on the left, read its profile and results. */
export function Competitors() {
  const L = useL();
  const navigate = useNavigate();
  const { id } = useParams();
  const [rows, setRows] = useState<CompetitorKb[] | null>(null);

  useEffect(() => {
    let alive = true;
    void edrak.competitorKbStatus().then((r) => { if (alive) setRows(r); });
    return () => { alive = false; };
  }, []);

  // Refresh while builds run so each competitor opens the moment it is ready.
  const building = rows !== null && isBuilding(rows);
  useEffect(() => {
    if (!building) return;
    const t = setInterval(() => {
      void edrak.competitorKbStatus().then(setRows).catch(() => undefined);
    }, 2000);
    return () => clearInterval(t);
  }, [building]);

  // Open the first ready competitor when none is chosen.
  useEffect(() => {
    if (!id && rows) {
      const first = rows.find((r) => READY.has(r.status));
      if (first) navigate(`/competitors/${first.entity_id}`, { replace: true });
    }
  }, [id, rows, navigate]);

  const chrome = (
    <PageChrome
      crumbs={[{ label: L("Competitors", "المنافسون") }]}
      title={L("Competitors", "المنافسون")}
    />
  );

  if (rows === null) {
    return <>{chrome}<div className="page-pad"><div className="shim" style={{ height: 320, borderRadius: 16 }} aria-busy="true" /></div></>;
  }
  if (rows.length === 0) {
    return (
      <>
        {chrome}
        <div className="page-pad">
          <div className="empty stack gap8">
            <span>{L("You are not tracking any competitors yet.", "لا تتابع أي منافسين بعد.")}</span>
            <Link className="btn pri" to="/onboarding?step=4&return=/competitors">{L("Add competitors", "أضف منافسين")}</Link>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      {chrome}
      <div className="page-pad kb-page">
        <aside className="kb-side">
          {rows.map((r) => (
            <button key={r.entity_id} type="button" className={`kb-side-item ${r.entity_id === id ? "on" : ""}`}
              disabled={!READY.has(r.status)} onClick={() => navigate(`/competitors/${r.entity_id}`)}>
              <span className="comp-sq">{r.name[0]?.toUpperCase()}</span>
              <span className="stack grow" style={{ minWidth: 0, textAlign: "start" }}>
                <bdi className="b6 ellip">{r.name}</bdi>
                <span className="s12 t3">
                  {READY.has(r.status) ? `${r.page_count} ${L("pages", "صفحات")}`
                    : r.status === "failed" ? L("Could not build", "تعذر البناء")
                    : r.status === "missing" ? L("Not built yet", "لم يُبنَ بعد")
                    : `${L("Building", "قيد البناء")} ${r.progress}%`}
                </span>
              </span>
            </button>
          ))}
        </aside>
        {(isBuilding(rows) || rows.some((r) => r.status === "failed" || r.status === "missing")) && (
          <CompetitorKbProgress compact onDone={setRows} />
        )}
        <main className="card pad grow" style={{ minWidth: 0 }}>
          {id ? <CompetitorProfileView entityId={id} />
            : <div className="empty">{L("Profiles appear here as soon as a build finishes.", "تظهر الملفات هنا فور انتهاء البناء.")}</div>}
        </main>
      </div>
    </>
  );
}
