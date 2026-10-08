/**
 * Every icon the design uses. Paths are copied from edrak-ui-handoff/screens, drawn on
 * a 24 px grid with a 1.7 stroke. Add a path here rather than inlining an <svg>.
 */
const PATHS = {
  plus: "M12 5v14M5 12h14",
  home: "M3 11l9-8 9 8M5 10v10h14V10M10 20v-6h4v6",
  building: "M4 21V5l8-2v18M12 9l8 2v10M4 21h16M8 8h.01M8 12h.01M8 16h.01M16 14h.01M16 17h.01",
  target: "M12 3a9 9 0 100 18 9 9 0 000-18M12 8a4 4 0 100 8 4 4 0 000-8M12 12h.01",
  trend: "M3 17l6-6 4 4 8-8M15 7h6v6",
  users: "M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7M2 20c0-3.5 3-6 7-6s7 2.5 7 6M16 4.5a3.5 3.5 0 010 6.5M18 14c2.5.6 4 2.5 4 6",
  arrow: "M5 12h14M13 6l6 6-6 6",
  chevron: "M9 6l6 6-6 6",
  chevronDown: "M6 9l6 6 6-6",
  x: "M6 6l12 12M18 6L6 18",
  check: "M5 12.5l4.5 4.5L19 7.5",
  checks: "M2 12.5l4.5 4.5L15 8M11 16l1 1L22 7",
  pause: "M8 5v14M16 5v14",
  refresh: "M4 12a8 8 0 0114-5l2 2M20 4v5h-5M20 12a8 8 0 01-14 5l-2-2M4 20v-5h5",
  warning: "M12 4l9 16H3zM12 10v4M12 17h.01",
  file: "M7 3h7l4 4v14H7zM14 3v4h4",
  fileLines: "M7 3h7l4 4v14H7zM14 3v4h4M9.5 12h5M9.5 16h5",
  upload: "M12 16V5M7 9l5-5 5 5M5 20h14",
  globe: "M12 3a9 9 0 100 18 9 9 0 000-18M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  signOut: "M10 4H5v16h5M15 8l4 4-4 4M19 12H9",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4",
  layers: "M12 3l9 5-9 5-9-5zM3 12.5l9 5 9-5M3 17l9 5 9-5",
  info: "M12 4a8 8 0 100 16 8 8 0 000-16M12 11v5M12 8h.01",
  play: "M7 5l12 7-12 7z",
  branch: "M6 4v12M6 16a3 3 0 100 6 3 3 0 000-6M18 9a3 3 0 100-6 3 3 0 000 6M18 9c0 5-6 4-12 7",
  lock: "M6 11h12v9H6zM9 11V8a3 3 0 016 0v3",
  link: "M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3A4 4 0 0010 18.7l1-1",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  copy: "M9 9h11v11H9zM5 15V4h11",
} as const;

export type IconName = keyof typeof PATHS;

interface IconProps {
  name: IconName;
  size?: number;
  /** 1.7 everywhere in the design, except small check marks that use 2.4 to 2.6. */
  stroke?: number;
  className?: string;
  /** Mirror in right-to-left layouts. Use for arrows and chevrons that point along the text. */
  flip?: boolean;
}

export function Icon({ name, size = 16, stroke = 1.7, className = "", flip = false }: IconProps) {
  return (
    <svg
      className={`ic ${flip ? "flip" : ""} ${className}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
