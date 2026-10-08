import { useI18n, type TKey } from "../../i18n";
import { workerKey } from "../../lib/workers";
import type { Brief } from "../../types/app";
import { Chip } from "../ui/Chip";
import { Icon } from "../ui/Icon";

const WAYS: Record<number, TKey> = {
  1: "brief.sec.4.title.1",
  2: "brief.sec.4.title.2",
  3: "brief.sec.4.title.3",
  4: "brief.sec.4.title.4",
};

export function OptionsCard({ brief }: { brief: Brief }) {
  const { t } = useI18n();
  const steps = [...brief.next_steps, t("brief.step.choose")];

  return (
    <div className="col bc-fill">
      <div className="row as jb bc-a bc-head">
        <div className="col g8">
          <div className="eyebrow tb">{t("brief.sec.4.eyebrow")}</div>
          <h2 className="disp" style={{ fontSize: 34, margin: 0, maxWidth: 1000 }}>
            {WAYS[brief.options.length] ? t(WAYS[brief.options.length]) : t("brief.sec.4.title.n", { n: brief.options.length })}
          </h2>
        </div>
        <span style={{ marginTop: 8 }}>
          <Chip tone="line" icon="lock">
            {t("brief.decision")}
          </Chip>
        </span>
      </div>

      <div className="row bc-b option-row">
        {brief.options.map((option) => (
          <article key={option.option_id} className="card col option">
            <div className="row g10">
              <span className="option-key">{option.key}</span>
              <span className="b6" dir="auto" style={{ fontSize: 17 }}>
                {option.title}
              </span>
            </div>
            <div className="t2 lh14" dir="auto" style={{ fontSize: 16.5 }}>
              {option.description}
            </div>
            <hr className="rule" />
            <div style={{ fontSize: 14 }}>
              <span className="t3">{t("brief.supportedBy")}</span>
              <div className="lh14" dir="auto" style={{ marginTop: 3 }}>
                {option.supported_by}
              </div>
            </div>
            <div style={{ fontSize: 14 }}>
              <span className="t3">{t("brief.wouldChange")}</span>
              <div className="lh14" dir="auto" style={{ marginTop: 3 }}>
                {option.would_change_if}
              </div>
            </div>
          </article>
        ))}
      </div>

      <div className="row as bc-c options-bottom">
        <div className="col grow options-col">
          <div className="eyebrow">{t("brief.gaps")}</div>
          {brief.gaps.length === 0 ? (
            <div className="s13 t2">{t("brief.noGaps")}</div>
          ) : (
            brief.gaps.map((gap) => (
              <div key={`${gap.reported_by}-${gap.text}`} className="hatch gap-row">
                <span className="gap-icon">
                  <Icon name="warning" size={16} />
                </span>
                <div className="grow">
                  <div className="b6 s14 lh12" dir="auto">{gap.text}</div>
                  <div className="s13 t2" style={{ marginTop: 2 }}>
                    {gap.reported_by === "verification"
                      ? t("brief.reportedBy.verification")
                      : t("brief.reportedBy.worker", { worker: t(`worker.name.${workerKey(gap.reported_by)}` as TKey) })}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
        <div className="col grow options-col">
          <div className="eyebrow" style={{ marginBottom: 2 }}>
            {t("brief.nextSteps")}
          </div>
          {steps.map((step, index) => (
            <div key={step} className="row as g10" style={{ padding: "14px 0", borderBottom: "1px solid var(--hair)" }}>
              <span className="mono tb" style={{ width: 22, flex: "none", paddingTop: 3 }}>
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="lh14 grow" dir="auto" style={{ fontSize: 15 }}>
                {step}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
