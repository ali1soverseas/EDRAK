/**
 * The Edrak mark, drawn inline so it can move: it turns slowly while Edrak is thinking and its
 * nodes pulse in sequence. Static when `live` is false or the person prefers reduced motion.
 */
const SPOKES: ReadonlyArray<readonly [number, number]> = [
  [85.7, 81], [133.7, 95], [147.7, 143], [133.7, 191], [85.7, 205], [37.7, 191], [23.7, 143], [37.7, 95],
];

export function ThinkingMark({ size = 28, live = true, color = "currentColor" }: { size?: number; live?: boolean; color?: string }) {
  return (
    <svg className={`tmark ${live ? "live" : ""}`.trim()} width={size} height={size} viewBox="4.7 62 162 162" aria-hidden="true">
      <g className="tmark-spin">
        {SPOKES.map(([x, y]) => <line key={`l${x}-${y}`} x1="85.7" y1="143" x2={x} y2={y} stroke={color} strokeWidth="10" strokeLinecap="round" />)}
        {SPOKES.map(([x, y], i) =>
          i % 2 === 0
            ? <rect key={`n${i}`} className={`tn tn${i + 1}`} x={x - 15} y={y - 15} width="30" height="30" rx="8.5" fill={color} />
            : <circle key={`n${i}`} className={`tn tn${i + 1}`} cx={x} cy={y} r="14" fill={color} />)}
        <circle cx="85.7" cy="143" r="20" fill={color} />
      </g>
    </svg>
  );
}
