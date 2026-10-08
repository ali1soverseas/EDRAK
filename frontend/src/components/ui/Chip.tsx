import type { CSSProperties, ReactNode } from "react";
import { useI18n, type TKey } from "../../i18n";
import type { AnalysisStatus } from "../../types/app";
import { Icon, type IconName } from "./Icon";

interface ChipProps {
  /** One of the `c-*` classes from edrak.css, without the prefix: ok, run, bad, ins, ... */
  tone: "draft" | "await" | "run" | "ok" | "ins" | "rev" | "dec" | "bad" | "line" | "blue";
  icon?: IconName;
  /** Size of the icon. 13 for normal chips, 11 for the small chips in finding rows. */
  iconSize?: number;
  /** Rotate the icon. Used for the running status. */
  spinning?: boolean;
  style?: CSSProperties;
  children: ReactNode;
}

export function Chip({ tone, icon, iconSize = 13, spinning = false, style, children }: ChipProps) {
  return (
    <span className={`chip c-${tone}`} style={style}>
      {icon &&
        (spinning ? (
          <span className="spin-fast">
            <Icon name={icon} size={iconSize} />
          </span>
        ) : (
          <Icon name={icon} size={iconSize} stroke={iconSize <= 11 ? 2.6 : 1.7} />
        ))}
      {children}
    </span>
  );
}

const STATUS: Record<AnalysisStatus, { tone: ChipProps["tone"]; icon: IconName; label: TKey; spinning?: boolean }> = {
  draft: { tone: "draft", icon: "file", label: "status.draft" },
  awaiting_approval: { tone: "await", icon: "pause", label: "status.awaiting" },
  running: { tone: "run", icon: "refresh", label: "status.running", spinning: true },
  completed: { tone: "ok", icon: "check", label: "status.verified" },
  partial: { tone: "ins", icon: "warning", label: "status.partial" },
  failed: { tone: "bad", icon: "x", label: "status.failed" },
  cancelled: { tone: "draft", icon: "x", label: "status.cancelled" },
};

/** The status chip shared by the Analyses list and the screen headers. */
export function StatusChip({ status }: { status: AnalysisStatus }) {
  const { t } = useI18n();
  const config = STATUS[status];
  return (
    <Chip tone={config.tone} icon={config.icon} spinning={config.spinning}>
      {t(config.label)}
    </Chip>
  );
}
