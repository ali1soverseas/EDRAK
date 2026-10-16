import { useI18n, type TKey } from "../../i18n";
import { USE_CASE_META } from "../../lib/usecases";
import { WORKER_META, workerKey } from "../../lib/workers";
import type { Brief } from "../../types/app";
import { Chip } from "../ui/Chip";
import { Icon } from "../ui/Icon";
import { WorkerBadge } from "../ui/WorkerBadge";
import { RichText } from "./shared";

const briefNumber = (n: number) => String(n).padStart(4, "0");

/** "category trends and regulation were not checked", for a worker that failed. */
const NOT_CHECKED: Record<"int" | "comp" | "mkt" | "cust", TKey> = {
  int: "brief.notChecked.int",
  comp: "brief.notChecked.comp",
  mkt: "brief.notChecked.mkt",
  cust: "brief.notChecked.cust",
};

export function SummaryCard({ brief }: { brief: Brief }) {
  const { t, tn, formatDate } = useI18n();
  const meta = USE_CASE_META[brief.use_case];
  const failed = brief.lenses.filter((lens) => lens.failed);
  const reported = brief.lenses.length - failed.length;
  const gapCount = brief.gaps.length;
  const sources = brief.evidence.length;

  const limits = brief.summary.limits ?? brief.gaps.map((gap) => gap.text).join(". ");

  return (
    <div className="row as summary-grid">
      <div className="col bc-a summary-left">
        <div className="col" style={{ gap: 16 }}>
          <div className="eyebrow tb">{t("brief.sec.1.eyebrow")}</div>
          <div className="row g10 wrap">
            <Chip tone="dec" icon="sparkle">
              {t(meta.short)}
            </Chip>
            <span className="mono t3">{t("brief.no", { no: briefNumber(brief.brief_no) })}</span>
            <span className="t3">·</span>
            <span className="mono t3">{formatDate(brief.created_at)}</span>
          </div>
          <h1 className="disp" dir="auto" style={{ fontSize: 44, margin: 0 }}>
            {brief.title}
          </h1>
          <div className="row g8 wrap">
            {brief.partial ? (
              <Chip tone="ins" icon="warning">
                {t("status.partial")}
              </Chip>
            ) : (
              <>
                <Chip tone="ok" icon="check">
                  {t("status.verified")}
                </Chip>
                {gapCount > 0 && (
                  <Chip tone="ins" icon="warning">
                    {tn("plural.gapsInfo", gapCount)}
                  </Chip>
                )}
              </>
            )}
            <Chip tone="line" icon="layers">
              {brief.partial
                ? t("brief.sourcesPartial", { sources: tn("plural.sources", sources), reported, total: brief.lenses.length })
                : t("brief.sourcesAll", { sources: tn("plural.sources", sources), workers: tn("plural.workers", brief.lenses.length) })}
            </Chip>
          </div>
          <div className="worker-grid">
            {brief.lenses.map((lens) => (
              <div key={lens.worker} className={`worker-pill ${lens.failed ? "failed" : ""}`.trim()}>
                <WorkerBadge worker={lens.worker} iconSize={12} />
                <span className="s12 b6 grow" style={{ whiteSpace: "nowrap" }}>
                  {t(`worker.short.${workerKey(lens.worker)}` as TKey)}
                </span>
                <span style={{ color: lens.failed ? "var(--bad)" : "var(--ok)", display: "inline-flex" }}>
                  <Icon name={lens.failed ? "x" : "check"} size={14} stroke={2.4} />
                </span>
              </div>
            ))}
          </div>
        </div>

        {brief.partial ? (
          <div className="limits bad">
            <Icon name="warning" size={18} stroke={1.8} />
            <div className="s13 lh14 grow">
              <b>{t("brief.partial.title")}</b>{" "}
              {failed
                .map((lens) => t("brief.partial.failed", { worker: t(`worker.name.${workerKey(lens.worker)}` as TKey), not: t(NOT_CHECKED[WORKER_META[lens.worker].css]) }))
                .join(" ")}{" "}
              {t("brief.partial.rests", { reported, total: brief.lenses.length })}
            </div>
          </div>
        ) : (
          gapCount > 0 &&
          limits && (
            <div className="limits hatch">
              <Icon name="warning" size={18} stroke={1.8} />
              <div className="s13 lh14 grow">
                <b>{t("brief.limits")}</b> <bdi>{limits}</bdi>
              </div>
            </div>
          )
        )}
      </div>

      <div className="grow bc-b summary-right">
        <div className="summary-block">
          <div className="mono tb">{t("brief.found")}</div>
          <div className="serif summary-text" dir="auto">
            <RichText segments={brief.summary.found} />
          </div>
        </div>
        <div className="summary-block">
          <div className="mono tb">{t("brief.matters")}</div>
          <div className="serif summary-text" dir="auto">
            <RichText segments={brief.summary.matters} />
          </div>
        </div>
        <div className="summary-block">
          <div className="mono tb">{t("brief.optionsLabel")}</div>
          <div className="serif summary-text" dir="auto">
            <RichText segments={brief.summary.options} />
          </div>
        </div>
      </div>
    </div>
  );
}
