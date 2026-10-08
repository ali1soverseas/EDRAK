import type { CSSProperties } from "react";
import full from "../../assets/logos/edrak-logo-v3.svg";
import reversed from "../../assets/logos/edrak-logo-v3-reversed.svg";
import mark from "../../assets/logos/edrak-mark-v3.svg";

const SOURCES = { full, reversed, mark } as const;

interface LogoProps {
  /** `full` is the blue wordmark, `reversed` the light one for dark panels, `mark` the symbol alone. */
  variant: keyof typeof SOURCES;
  height: number;
  style?: CSSProperties;
}

export function Logo({ variant, height, style }: LogoProps) {
  return <img src={SOURCES[variant]} alt="edrak" style={{ height, width: "auto", ...style }} />;
}
