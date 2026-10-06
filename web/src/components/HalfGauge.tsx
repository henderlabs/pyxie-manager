"use client";

import { useEffect, useState } from "react";
import { COLOR_MUTED, COLOR_TRACK, colorForPct } from "@/components/Gauges";

// Dashboard gauge: a 180-degree "half-moon" arc, flat side down. Same traffic-light
// colours and track as the rest of PyXie (green below 75, amber from 75, red from 90);
// two small ticks on the track mark where amber and red begin. The arc is drawn with
// pathLength=100 so the dash length is simply the percentage.
export function HalfGauge({
  value,
  label,
  detail,
  icon,
  width = 140,
  strokeWidth = 10,
  ticks = [75, 90],
  scale = "usage",
  title,
}: {
  value: number | null;
  label: string;
  detail?: string | null;
  icon?: React.ReactNode;
  width?: number;
  strokeWidth?: number;
  /** Where the two threshold ticks sit on the track (default 75 and 90 -- amber, red). */
  ticks?: number[];
  /** "usage": high is bad (green below 75, amber from 75, red from 90). "balance": high is good
   *  (green from 80, amber from 60, red below). A name, not a function, because this component is
   *  rendered from server pages and functions cannot cross that boundary. */
  scale?: "usage" | "balance";
  title?: string;
}) {
  const pct = value === null ? 0 : Math.min(Math.max(value, 0), 100);
  // Sweep in from empty on first paint; motion-reduce users get the value at once.
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(pct));
    return () => cancelAnimationFrame(id);
  }, [pct]);

  const pad = strokeWidth / 2 + 4; // room for the round caps and the tick ends
  const r = width / 2 - pad;
  const cx = width / 2;
  const cy = r + pad;
  const height = cy + strokeWidth / 2 + 2;
  const d = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`;
  const tick = (p: number) => {
    const t = Math.PI * (1 - p / 100);
    const at = (rad: number) => [cx + rad * Math.cos(t), cy - rad * Math.sin(t)];
    const [x1, y1] = at(r - strokeWidth / 2 - 1);
    const [x2, y2] = at(r + strokeWidth / 2 + 1);
    return { x1, y1, x2, y2 };
  };

  return (
    <div className="flex flex-col items-center">
      <div
        className="relative"
        style={{ width, height }}
        role="meter"
        title={title}
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value === null ? undefined : Math.round(pct)}
      >
        <svg width={width} height={height} aria-hidden="true">
          <path d={d} fill="none" stroke={COLOR_TRACK} strokeWidth={strokeWidth} strokeLinecap="round" />
          {value !== null && pct > 0 && (
            <path
              d={d}
              fill="none"
              stroke={scale === "balance" ? (value >= 80 ? "#2fbf71" : value >= 60 ? "#e5a94c" : "#e5484d") : colorForPct(value)}
              strokeWidth={strokeWidth}
              strokeLinecap="round"
              pathLength={100}
              strokeDasharray={`${shown} 100`}
              className="transition-[stroke-dasharray] duration-500 ease-out motion-reduce:transition-none"
            />
          )}
          {ticks.map((p) => {
            const t = tick(p);
            return <line key={p} {...t} stroke={COLOR_MUTED} strokeOpacity={0.7} strokeWidth={1.5} />;
          })}
        </svg>
        <div className="absolute inset-x-0 bottom-0.5 text-center text-[22px] font-semibold leading-none text-text">
          {value !== null ? `${Math.round(pct)}%` : "—"}
        </div>
      </div>
      <div className="text-xs text-muted mt-2 text-center flex items-center gap-1.5">
        {icon && <span className="text-proxmox/70">{icon}</span>}
        {label}
      </div>
      {detail && <div className="text-[11px] text-muted/80 mt-0.5 text-center tabular-nums">{detail}</div>}
    </div>
  );
}
