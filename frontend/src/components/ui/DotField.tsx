import { useEffect, useRef } from "react";

/**
 * A grid of dots behind the beige surfaces that moves like a slow wave.
 *
 * One fixed <canvas>; each dot's offset and opacity follow a travelling sine wave. It draws at most
 * ~30 frames a second, pauses while the tab is hidden, and stays still under prefers-reduced-motion.
 * A small canvas loop rather than an animation library, as the design asks of motion.
 */
export function DotField({ spacing = 22, opacity = 1, className = "" }: { spacing?: number; opacity?: number; className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let last = 0;
    let width = 0;
    let height = 0;

    const color = () => getComputedStyle(document.documentElement).getPropertyValue("--ink").trim() || "#0B0D38";

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const draw = (time: number) => {
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = color();
      const t = time / 1000;
      for (let y = spacing / 2; y < height + spacing; y += spacing) {
        for (let x = spacing / 2; x < width + spacing; x += spacing) {
          // Two crossing waves: a slow diagonal swell and a gentler horizontal one.
          const wave = Math.sin(x * 0.012 + y * 0.008 - t * 0.9) + 0.5 * Math.sin(x * 0.02 - t * 0.55);
          const lift = (wave + 1.5) / 3; // 0..1
          const dy = wave * 3;
          ctx.globalAlpha = (0.05 + 0.13 * lift) * opacity;
          ctx.beginPath();
          ctx.arc(x, y + dy, 0.9 + 0.7 * lift, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    };

    const loop = (time: number) => {
      frame = requestAnimationFrame(loop);
      if (document.hidden || time - last < 33) return;
      last = time;
      draw(time);
    };

    resize();
    const onResize = () => {
      resize();
      if (reduced.matches) draw(0);
    };
    window.addEventListener("resize", onResize);
    if (reduced.matches) draw(0);
    else frame = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
    };
  }, [spacing, opacity]);

  return <canvas ref={ref} className={`dotfield ${className}`.trim()} aria-hidden="true" />;
}
