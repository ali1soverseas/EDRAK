import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAsync } from "../../lib/hooks";
import { SEEN_EVENT, isFinished, noticeFor, seenRun, stageLabel } from "../../lib/research";
import { useLatestRun } from "../../lib/useResearch";
import { useL } from "../../lib/useL";
import { edrak } from "../../services/edrak";
import { Icon } from "../ui/Icon";

/**
 * What the top bar says about company research: that it is running (with its step), that facts are waiting for review,
 * or that a run failed. It goes away once the person has opened the research page, and says nothing when there is
 * nothing to say.
 */
export function ResearchNotice() {
  const L = useL();
  const { run } = useLatestRun();
  const [seen, setSeen] = useState(seenRun);
  useEffect(() => {
    const update = () => setSeen(seenRun());
    window.addEventListener(SEEN_EVENT, update);
    return () => window.removeEventListener(SEEN_EVENT, update);
  }, []);
  const waiting = useAsync(
    () => (isFinished(run) && run?.status === "done" ? edrak.researchFacts({ status: ["suggested"] }).then((f) => f.total) : Promise.resolve(0)),
    [run?.id, run?.status],
  );
  const notice = noticeFor(run, waiting.data ?? 0, seen);
  if (!notice) return null;

  if (notice.kind === "running") {
    return (
      <Link to="/company/research" className="research-notice run" title={L("Company research is running. Open it.", "بحث الشركة جارٍ. افتحه.")}>
        <span className="spin-fast"><Icon name="refresh" size={14} /></span>
        <span className="research-notice-text">{L("Researching your company", "جارٍ البحث عن شركتك")} · {stageLabel(notice.stage, L)}</span>
      </Link>
    );
  }
  if (notice.kind === "review") {
    return (
      <Link to="/company/research" className="research-notice ready">
        <Icon name="sparkle" size={14} />
        <span className="research-notice-text">{L(`${notice.count} facts about your company to review`, `${notice.count} حقيقة عن شركتك للمراجعة`)}</span>
      </Link>
    );
  }
  return (
    <Link to="/company/research" className="research-notice bad">
      <Icon name="warning" size={14} />
      <span className="research-notice-text">{L("Company research failed", "فشل بحث الشركة")}</span>
    </Link>
  );
}
