import { useState, type CSSProperties } from "react";
import { useI18n } from "../../i18n";

interface ProgressBarProps {
  /** 0 to 100 */
  value: number;
  color: string;
  /**
   * Play the 2.4 s grow-in (`grow` in edrak.css) on first render. After it ends the bar
   * is driven by inline width with a 0.6 s transition, so polled updates do not jump.
   */
  growIn?: boolean;
}

export function ProgressBar({ value, color, growIn = false }: ProgressBarProps) {
  const [intro, setIntro] = useState(growIn);
  const style = { width: `${value}%`, "--to": `${value}%`, background: color } as CSSProperties;
  return (
    <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}>
      <i className={intro ? "grow-bar" : ""} style={style} onAnimationEnd={() => setIntro(false)} />
    </div>
  );
}

/** One to three marks. High evidence quality is three, medium two, low one. */
export function Pips({ value }: { value: 1 | 2 | 3 }) {
  const { t } = useI18n();
  return (
    <span className="pips" role="img" aria-label={t("brief.reliability", { n: value })}>
      {[1, 2, 3].map((n) => (
        <i key={n} className={n <= value ? "on" : ""} />
      ))}
    </span>
  );
}
