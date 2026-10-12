import type { BriefCard, CardStatus } from "../../types/brief3";
import { useL } from "../../lib/useL";
import { Icon } from "../ui/Icon";
import { CardBody } from "./charts";

export function StatusBadge({ status }: { status: CardStatus }) {
  const L = useL();
  const map: Record<CardStatus, [string, string, string]> = {
    verified: [L("Verified", "موثّق"), "", "c-ok"], thin: [L("Thin: one source", "ضعيف: مصدر واحد"), "", "c-ins"],
    conflict: [L("Sources disagree", "المصادر متعارضة"), "", "c-bad"], gap: [L("Gap", "فجوة"), "", "c-draft"], fallback: [L("Table", "جدول"), "", "c-line"],
  };
  const [label, , cls] = map[status] ?? map.fallback;
  return <span className={`chip ${cls}`}>{label}</span>;
}

/** Title, value, period, comparator, evidence chips, status badge and a chat button. Clicking opens the drawer. */
export function CardFrame({ card, onOpen, onAsk, onEvidence }: {
  card: BriefCard; onOpen: (key: string) => void; onAsk: (key: string) => void; onEvidence: (no: number) => void;
}) {
  const L = useL();
  return (
    <article className={`card3 st-${card.status} comp-${card.component}`}>
      <header className="row g8">
        <div className="stack grow" style={{ minWidth: 0 }}>
          <span className="eyebrow ellip">{card.title}</span>
          {card.headline && card.headline !== card.title && <bdi className="s13 b6">{card.headline}</bdi>}
        </div>
        <StatusBadge status={card.status} />
      </header>
      <div className="card3-body" role="button" tabIndex={0} onClick={() => onOpen(card.card_key)} onKeyDown={(e) => e.key === "Enter" && onOpen(card.card_key)}>
        <CardBody card={card} />
      </div>
      {card.caveat && <p className="s12 caveat"><Icon name="info" size={12} /> {card.caveat}</p>}
      <footer className="row g4 wrap">
        {card.derived && <span className="chip c-line" title={L("Computed by Edrak from stored figures. Tap for the formula.", "حُسب بواسطة إدراك. اضغط لرؤية المعادلة.")}>ƒ</span>}
        {card.refs.slice(0, 6).map((n) => <button key={n} type="button" className="ev" onClick={() => onEvidence(n)}>{`E${n}`}</button>)}
        {card.version && card.version > 1 && <span className="chip c-blue">v{card.version}</span>}
        <span className="grow" />
        {card.component !== "gap" && <button type="button" className="iconbtn" aria-label={L("Ask about this card", "اسأل عن هذه البطاقة")} onClick={() => onAsk(card.card_key)}><Icon name="sparkle" size={14} /></button>}
      </footer>
    </article>
  );
}
