const STAGE = { w: 720, h: 500 };
const CENTER = { x: 360, y: 250 };
const RADIUS = 176;
const PULSE_LENGTH = 36;
const PULSE_SECONDS = 3.2;
const STAGGER_SECONDS = 0.35;
const PULSE_START_SECONDS = 0.9;

type Side = "top" | "right" | "bottom" | "left";
type IconName =
  | "doc"
  | "link"
  | "users"
  | "check"
  | "chart"
  | "spark"
  | "target"
  | "shield";

const NODES: Array<{
  label: string;
  angle: number;
  side: Side;
  icon: IconName;
  gold?: boolean;
}> = [
  { label: "Internal", angle: -90, side: "top", icon: "doc" },
  { label: "Verification", angle: -45, side: "right", icon: "shield" },
  { label: "Competitors", angle: 0, side: "right", icon: "target" },
  { label: "Synthesis", angle: 45, side: "right", icon: "spark" },
  { label: "Market", angle: 90, side: "bottom", icon: "chart" },
  { label: "Your approval", angle: 135, side: "left", icon: "check", gold: true },
  { label: "Customers", angle: 180, side: "left", icon: "users" },
  { label: "Sources", angle: 225, side: "left", icon: "link" },
];

const placedNodes = NODES.map((node, index) => {
  const radians = (node.angle * Math.PI) / 180;
  return {
    ...node,
    index,
    x: CENTER.x + Math.cos(radians) * RADIUS,
    y: CENTER.y + Math.sin(radians) * RADIUS,
  };
});

function NodeIcon({ name }: { name: IconName }) {
  const props = {
    width: 14,
    height: 14,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true as const,
  };

  switch (name) {
    case "doc":
      return (
        <svg {...props}>
          <path d="M7 3.5h6.5L18 8v11.2a1.3 1.3 0 0 1-1.3 1.3H7.3A1.3 1.3 0 0 1 6 19.2V4.8A1.3 1.3 0 0 1 7.3 3.5H7z" />
          <path d="M13.2 3.7V8H17.6" />
          <path d="M9 12.2h6M9 15.4h4.2" />
        </svg>
      );
    case "link":
      return (
        <svg {...props}>
          <path d="M10 13.2a3.6 3.6 0 0 0 5.1.4l2.2-2.2a3.6 3.6 0 0 0-5.1-5.1l-1.2 1.2" />
          <path d="M14 10.8a3.6 3.6 0 0 0-5.1-.4l-2.2 2.2a3.6 3.6 0 0 0 5.1 5.1l1.2-1.2" />
        </svg>
      );
    case "users":
      return (
        <svg {...props}>
          <circle cx="9" cy="9" r="2.4" />
          <path d="M4.8 17.2c.5-2.2 2.2-3.4 4.2-3.4s3.7 1.2 4.2 3.4" />
          <circle cx="16" cy="9.4" r="2" />
          <path d="M15.2 13.8c1.5.2 2.7 1.1 3.2 2.8" />
        </svg>
      );
    case "check":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="7.2" />
          <path d="M8.6 12.2 11 14.5l4.5-5" />
        </svg>
      );
    case "chart":
      return (
        <svg {...props}>
          <path d="M4.5 16.5 9.2 11l3.1 2.6L19 7.2" />
          <path d="M14.6 7.2H19V11.4" />
        </svg>
      );
    case "spark":
      return (
        <svg {...props}>
          <path d="M12 3.5 13.4 9 19 10.4 13.4 11.8 12 17.2 10.6 11.8 5 10.4 10.6 9z" />
          <path d="M17.2 15.2 17.8 17 19.6 17.6 17.8 18.2 17.2 20 16.6 18.2 14.8 17.6 16.6 17z" />
        </svg>
      );
    case "target":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="6.6" />
          <circle cx="12" cy="12" r="2.3" />
          <path d="M12 3.2V6M12 18v2.6M3.2 12H6M18 12h2.6" />
        </svg>
      );
    case "shield":
      return (
        <svg {...props}>
          <path d="M12 3.4 18.2 6v5.2c0 3.6-2.4 6.4-6.2 8.2-3.8-1.8-6.2-4.6-6.2-8.2V6z" />
          <path d="M9.1 11.8 11.1 13.7 15 9.6" />
        </svg>
      );
  }
}

export function DecisionGraph() {
  return (
    <div
      className="stage"
      role="img"
      aria-label="Animated network of Edrak signals around one decision: Internal, Sources, Customers, Your approval, Market, Synthesis, Competitors, and Verification."
    >
      <svg
        className="graph-svg"
        viewBox={`0 0 ${STAGE.w} ${STAGE.h}`}
        aria-hidden="true"
      >
        {placedNodes.map((node) => (
          <line
            key={`${node.label}-line`}
            x1={CENTER.x}
            y1={CENTER.y}
            x2={node.x}
            y2={node.y}
            className="spoke"
            strokeDasharray={RADIUS}
            strokeDashoffset={RADIUS}
            style={{ animationDelay: `${node.index * 0.07}s` }}
          />
        ))}
        {placedNodes.map((node) => {
          const begin = `${PULSE_START_SECONDS + node.index * STAGGER_SECONDS}s`;
          return (
            <g key={`${node.label}-pulse`} className="pulse-dot">
              <line
                x1={CENTER.x}
                y1={CENTER.y}
                x2={node.x}
                y2={node.y}
                stroke="#d5defe"
                strokeWidth={5}
                strokeLinecap="round"
                strokeDasharray={`${PULSE_LENGTH} ${RADIUS}`}
                opacity={0}
              >
                <animate
                  attributeName="stroke-dashoffset"
                  from={PULSE_LENGTH}
                  to={-RADIUS}
                  dur={`${PULSE_SECONDS}s`}
                  begin={begin}
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="opacity"
                  values="0;1;1;0"
                  keyTimes="0;0.12;0.76;1"
                  dur={`${PULSE_SECONDS}s`}
                  begin={begin}
                  repeatCount="indefinite"
                />
              </line>
            </g>
          );
        })}
      </svg>

      <div
        className="hub"
        style={{
          left: `${(CENTER.x / STAGE.w) * 100}%`,
          top: `${(CENTER.y / STAGE.h) * 100}%`,
        }}
      >
        <span className="hub-core" />
      </div>

      {placedNodes.map((node) => (
        <div
          key={node.label}
          className={`node node-${node.side}`}
          style={{
            left: `${(node.x / STAGE.w) * 100}%`,
            top: `${(node.y / STAGE.h) * 100}%`,
            animationDelay: `${0.12 + node.index * 0.06}s`,
          }}
        >
          <span className={node.gold ? "pill pill-gold" : "pill"}>
            <NodeIcon name={node.icon} />
            {node.label}
          </span>
          <span
            className="node-sq"
            style={{
              animationDelay: `${3.2 + node.index * STAGGER_SECONDS}s`,
            }}
          />
        </div>
      ))}
    </div>
  );
}
