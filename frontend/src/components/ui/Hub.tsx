/**
 * The hub diagram: eight spokes around a centre with eight rounded nodes.
 *
 * Geometry and animation are the markup from screens/signin.html. The motion is all
 * CSS in edrak.css (`.hub.live`): a bright pulse travels out along each spoke
 * (`travel`, 2.4 s) and each node grows to 1.5x and back (`nodep`, 2.4 s), both offset
 * 0.3 s per spoke so it goes around the circle. Do not rebuild it with a JS animation.
 */
const ENDS: ReadonlyArray<readonly [number, number]> = [
  [24, 6],
  [36.4, 11.6],
  [42, 24],
  [36.4, 36.4],
  [24, 42],
  [11.6, 36.4],
  [6, 24],
  [11.6, 11.6],
];

interface HubProps {
  size: number;
  color: string;
  /** Spoke stroke width in viewBox units. 0.7 for the large sign-in diagram, 2.4 for the 22 px icon. */
  line: number;
  /** Node square size in viewBox units. 3.6 large, 7.2 small. */
  node: number;
  /** Radius of the centre dot. 1.9 large, 3.6 small. */
  center: number;
  /** Animate. Turn off when the thing it stands for is not running. */
  live?: boolean;
}

export function Hub({ size, color, line, node, center, live = true }: HubProps) {
  return (
    <svg className={`hub ${live ? "live" : ""}`.trim()} width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
      {ENDS.map(([x, y]) => (
        <line key={`base-${x}-${y}`} x1="24" y1="24" x2={x} y2={y} stroke={color} strokeOpacity=".3" strokeWidth={line} strokeLinecap="round" />
      ))}
      {live &&
        ENDS.map(([x, y], index) => (
          <line
            key={`pulse-${x}-${y}`}
            className="sp"
            style={{ animationDelay: `${(index * 0.3).toFixed(1)}s` }}
            pathLength={1}
            x1="24"
            y1="24"
            x2={x}
            y2={y}
            stroke={color}
            strokeWidth={line}
            strokeLinecap="round"
          />
        ))}
      {ENDS.map(([x, y], index) => (
        <rect
          key={`node-${x}-${y}`}
          className={`hn hn${index + 1}`}
          x={x - node / 2}
          y={y - node / 2}
          width={node}
          height={node}
          rx={node * 0.3}
          fill={color}
        />
      ))}
      <circle cx="24" cy="24" r={center} fill={color} />
    </svg>
  );
}
