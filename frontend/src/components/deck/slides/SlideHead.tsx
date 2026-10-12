import type { ReactNode } from "react";

/** "02 · BUSINESS METRICS", the slide title, and anything that sits on the right. */
export function SlideHead({ no, name, title, sub, aside }: { no: number; name: string; title?: string; sub?: string; aside?: ReactNode }) {
  return (
    <div className="slide-head">
      <div className="stack gap8 grow" style={{ minWidth: 0 }}>
        <div className="eyebrow tb">{String(no).padStart(2, "0")} · {name}</div>
        {title && <h2 className="disp slide-title" dir="auto">{title}</h2>}
        {sub && <p className="t2 s14 lh14 slide-sub" dir="auto">{sub}</p>}
      </div>
      {aside && <div className="slide-aside">{aside}</div>}
    </div>
  );
}
