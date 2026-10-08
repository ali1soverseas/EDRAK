import { useNavigate, useParams } from "react-router-dom";
import { ReportView } from "../components/ReportView";
import { PageChrome } from "../components/shell/AppShell";
import { Icon } from "../components/ui/Icon";
import { useI18n } from "../i18n";
import { useAsync } from "../lib/hooks";
import { api } from "../services/api";

const briefNumber = (n: number) => String(n).padStart(4, "0");

export function Report() {
  const { t } = useI18n();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const brief = useAsync(() => api.getBrief(id), [id]);
  const data = brief.data;

  const crumbs = [
    { label: t("nav.analyses"), to: "/" },
    { label: data?.analysis_title ?? "…" },
    { label: data ? t("brief.no", { no: briefNumber(data.brief_no) }) : t("brief.crumb") },
  ];
  // The brief uses the narrow rail and owns its scrolling.
  const chrome = <PageChrome crumbs={crumbs} rail flush title={data ? t("brief.no", { no: briefNumber(data.brief_no) }) : t("brief.crumb")} />;

  if (brief.error) {
    return (
      <>
        {chrome}
        <div style={{ padding: "32px 40px" }}>
          <div className="form-error" role="alert">
            <Icon name="warning" size={16} />
            <span>{t("brief.notFound")}</span>
            <button type="button" className="btn sm" onClick={() => navigate("/")}>
              {t("plan.backToList")}
            </button>
          </div>
        </div>
      </>
    );
  }

  if (!data) {
    return (
      <>
        {chrome}
        <div className="col brief-page" aria-busy="true">
          <div className="shim" style={{ height: 36, width: 320 }} />
          <div className="shim" style={{ flex: 1, borderRadius: 20 }} />
        </div>
      </>
    );
  }

  return (
    <>
      {chrome}
      <ReportView brief={data} />
    </>
  );
}
