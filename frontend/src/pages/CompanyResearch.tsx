import { useEffect } from "react";
import { PageChrome } from "../components/shell/AppShell";
import { ResearchPanel } from "../components/company/ResearchPanel";
import { markSeen, isFinished } from "../lib/research";
import { useLatestRun } from "../lib/useResearch";
import { useL } from "../lib/useL";

/** Start company research, follow it, and review what it found. */
export function CompanyResearch() {
  const L = useL();
  const { run } = useLatestRun();
  // Opening this page counts as looking at the result, so the notice in the top bar clears.
  useEffect(() => { if (run && isFinished(run)) markSeen(run.id); }, [run?.id, run?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <PageChrome
        crumbs={[{ label: L("Company profile", "ملف الشركة"), to: "/company" }, { label: L("Company research", "بحث الشركة") }]}
        title={L("Company research", "بحث الشركة")}
      />
      <div className="page-pad">
        <header className="stack gap4">
          <h1 className="disp" style={{ margin: 0, fontSize: 30 }}>{L("Company research", "بحث الشركة")}</h1>
          <p className="t2 lh14" style={{ margin: 0, maxWidth: 760 }}>
            {L("What Edrak can find about your company from your own website and documents. Approve what is right, fix what is nearly right, and reject the rest.",
              "ما يمكن لإدراك معرفته عن شركتك من موقعك ومستنداتك. اعتمد الصحيح، وصحّح ما يقارب الصحة، وارفض الباقي.")}
          </p>
        </header>
        <ResearchPanel />
      </div>
    </>
  );
}
