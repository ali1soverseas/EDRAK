/**
 * The brief: four full-height cards you scroll through, a dot rail, and the source drawer.
 *
 * The brief container (`.bscroll`) must be the scroll element. Snapping, and the blur and
 * parallax between cards, are driven by CSS scroll timelines on it (see edrak.css). Do not
 * move the cards into the page scroll, and do not add `.bstatic`: that only exists so the
 * handoff screenshots could be taken.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useI18n, type TKey } from "../i18n";
import type { Brief, BriefEvidence } from "../types/app";
import { evidenceKey } from "../types/app";
import { EvidenceContext } from "./brief/shared";
import { EvidenceCard } from "./brief/EvidenceCard";
import { FindingsCard } from "./brief/FindingsCard";
import { OptionsCard } from "./brief/OptionsCard";
import { SummaryCard } from "./brief/SummaryCard";
import { EvidencePanel } from "./EvidencePanel";
import { Icon } from "./ui/Icon";

const SECTIONS: Array<{ title: TKey; description: TKey }> = [
  { title: "brief.sec.1.title", description: "brief.sec.1.desc" },
  { title: "brief.sec.2.name", description: "brief.sec.2.desc" },
  { title: "brief.sec.3.name", description: "brief.sec.3.desc" },
  { title: "brief.sec.4.name", description: "brief.sec.4.desc" },
];

const briefNumber = (n: number) => String(n).padStart(4, "0");

export function ReportView({ brief }: { brief: Brief }) {
  const { t } = useI18n();
  const scroller = useRef<HTMLDivElement>(null);
  const frame = useRef(0);
  const [active, setActive] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const [selected, setSelected] = useState<BriefEvidence | null>(null);

  const byKey = useMemo(
    () => new Map(brief.evidence.map((item) => [evidenceKey(item.worker, item.evidence.evidence_id), item])),
    [brief.evidence],
  );

  const open = useCallback((key: string) => setSelected(byKey.get(key) ?? null), [byKey]);
  const context = useMemo(() => ({ brief, byKey, open }), [brief, byKey, open]);

  const onScroll = () => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const element = scroller.current;
      if (!element || element.clientHeight === 0) return;
      setActive(Math.max(0, Math.min(SECTIONS.length - 1, Math.round(element.scrollTop / element.clientHeight))));
      setScrolled(element.scrollTop > 8);
    });
  };

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const goTo = (index: number) => {
    const element = scroller.current;
    if (!element) return;
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    element.scrollTo({ top: index * element.clientHeight, behavior: calm ? "auto" : "smooth" });
  };

  const sectionNames = SECTIONS.map((section) => t(section.title));

  return (
    <EvidenceContext.Provider value={context}>
      <div className="col brief-page">
        <div className="row jb brief-bar" style={{ height: 36 }}>
          <div className="row g10">
            <span className="mono t3">{t("brief.no", { no: briefNumber(brief.brief_no) })}</span>
            <span className="row g10 brief-requested">
              <span className="t3">·</span>
              <span className="s13 t3" dir="auto">
                {t("brief.requestedBy", { name: brief.requested_by })}
              </span>
            </span>
          </div>
          <div className="row g8">
            <span className="row g8 mono t3" aria-live="polite">
              <bdi className="tb" dir="ltr">{`${String(active + 1).padStart(2, "0")} / ${String(SECTIONS.length).padStart(2, "0")}`}</bdi>
              {sectionNames[active]}
            </span>
          </div>
        </div>

        <div className="bframe">
          <div ref={scroller} className="bscroll" tabIndex={0} onScroll={onScroll} aria-label={t("brief.scrollLabel")}>
            <section className="bcard" aria-label={sectionNames[0]}>
              <div className="bc-body">
                <SummaryCard brief={brief} />
              </div>
            </section>
            <section className="bcard" aria-label={sectionNames[1]}>
              <div className="bc-body">
                <FindingsCard brief={brief} />
              </div>
            </section>
            <section className="bcard" aria-label={sectionNames[2]}>
              <div className="bc-body">
                <EvidenceCard brief={brief} onOpen={setSelected} />
              </div>
            </section>
            <section className="bcard" aria-label={sectionNames[3]}>
              <div className="bc-body">
                <OptionsCard brief={brief} />
              </div>
            </section>
          </div>

          <div className="brail" role="group" aria-label={t("brief.rail")}>
            {SECTIONS.map((section, index) => {
              const on = index === active;
              return (
                <button
                  key={section.title}
                  type="button"
                  className="bdot"
                  aria-label={t("brief.goTo", { n: index + 1, name: sectionNames[index] })}
                  aria-current={on}
                  onClick={() => goTo(index)}
                >
                  <i style={{ width: 8, height: on ? 24 : 8, background: on ? "#4A495E" : "#CFCCC1" }} />
                  <span className="btip">
                    <span className="mono tb">{String(index + 1).padStart(2, "0")}</span>
                    <span className="b7" style={{ fontSize: 14, lineHeight: 1.25 }}>
                      {sectionNames[index]}
                    </span>
                    <span className="s12 t2 lh14">{t(section.description)}</span>
                  </span>
                </button>
              );
            })}
          </div>

          <div className="bhint" style={{ opacity: scrolled ? 0 : 1 }} aria-hidden="true">
            <span className="mono">{t("brief.hint")}</span>
            <Icon name="chevronDown" size={14} />
          </div>
        </div>
      </div>
      {selected && <EvidencePanel brief={brief} item={selected} onClose={() => setSelected(null)} />}
    </EvidenceContext.Provider>
  );
}
