import type { KeyboardEvent, MouseEvent, ReactNode } from "react";
import { useL } from "../../lib/useL";
import type { BriefCard, CardStatus } from "../../types/brief3";
import { CardBody } from "../dashboard/charts";
import { Icon } from "../ui/Icon";
import { useDeck } from "./context";

export function StatusBadge({ status }: { status: CardStatus | string }) {
  const L = useL();
  const map: Record<string, [string, string, string]> = {
    verified: [L("Verified", "موثّق"), "c-ok", "check"],
    thin: [L("Thin: one source", "ضعيف: مصدر واحد"), "c-ins", "warning"],
    conflict: [L("Sources disagree", "المصادر متعارضة"), "c-bad", "warning"],
    gap: [L("Gap", "فجوة"), "c-draft", "info"],
    fallback: [L("Table", "جدول"), "c-line", "layers"],
  };
  const [label, cls] = map[status] ?? map.fallback;
  return <span className={`chip ${cls}`}>{label}</span>;
}

/** E-number chips. A click opens that source and does not open the card underneath. */
export function EvChips({ refs, max = 5 }: { refs: number[]; max?: number }) {
  const { openEvidence } = useDeck();
  const L = useL();
  if (!refs.length) return null;
  return (
    <span className="row g4 wrap">
      {refs.slice(0, max).map((n) => (
        <button key={n} type="button" className="ev" aria-label={L(`Open source E${n}`, `افتح المصدر E${n}`)}
          onClick={(e) => { e.stopPropagation(); openEvidence(n); }}>{`E${n}`}</button>
      ))}
      {refs.length > max && <span className="s12 t3">+{refs.length - max}</span>}
    </span>
  );
}

/** Anything on a slide that opens a drawer: the whole surface is the button. */
export function Clickable({ onOpen, label, className = "", children }: { onOpen: () => void; label: string; className?: string; children: ReactNode }) {
  const onKey = (e: KeyboardEvent) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); }
  };
  return (
    <article className={`tap ${className}`.trim()} role="button" tabIndex={0} aria-label={label} onClick={onOpen} onKeyDown={onKey}>
      {children}
    </article>
  );
}

export function AskButton({ onAsk, label }: { onAsk: () => void; label: string }) {
  return (
    <button type="button" className="ask-chip" aria-label={label} title={label} onClick={(e: MouseEvent) => { e.stopPropagation(); onAsk(); }}>
      <Icon name="sparkle" size={13} />
    </button>
  );
}

/** A typed card: title, value or chart, period, comparator, evidence, status, and a chat button. */
export function InsightCard({ card, compact = false }: { card: BriefCard; compact?: boolean }) {
  const L = useL();
  const { openCard, ask } = useDeck();
  const gap = card.component === "gap";
  return (
    <Clickable onOpen={() => openCard(card.card_key)} label={card.title}
      className={`icard st-${card.status} comp-${card.component} ${compact ? "compact" : ""}`}>
      <header className="icard-head">
        <div className="stack grow" style={{ minWidth: 0 }}>
          <span className="eyebrow ellip">{card.title}</span>
          {!gap && card.headline && card.headline !== card.title && <span className="icard-headline" dir="auto">{card.headline}</span>}
        </div>
        <StatusBadge status={card.status} />
      </header>
      <div className="icard-body"><CardBody card={card} /></div>
      {card.caveat && <p className="icard-caveat"><Icon name="info" size={12} /> <span>{card.caveat}</span></p>}
      <footer className="icard-foot">
        <EvChips refs={card.refs} max={compact ? 3 : 5} />
        {card.derived && <span className="chip c-line" title={L("Computed by Edrak from stored figures. Open the card for the formula.", "حُسب من الأرقام المخزنة. افتح البطاقة لرؤية المعادلة.")}>ƒ {L("derived", "مشتق")}</span>}
        {card.version && card.version > 1 ? <span className="chip c-blue">v{card.version}</span> : null}
        <span className="grow" />
        {!gap && <AskButton label={L("Ask about this card", "اسأل عن هذه البطاقة")} onAsk={() => ask({ cardKey: card.card_key, title: card.title })} />}
        <Icon name="chevron" size={14} className="icard-go" flip />
      </footer>
    </Clickable>
  );
}
