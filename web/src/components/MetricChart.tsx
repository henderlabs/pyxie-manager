"use client";

import { useState } from "react";

export type ChartSeries = {
  label: string;
  color: string;
  /** One value per entry in `times`; null leaves a gap (PVE has no sample). */
  values: (number | null)[];
  /** Fill under the line. Use on one series per chart so overlaps stay readable. */
  area?: boolean;
};

const W = 600;
const H = 150;
const PAD = { l: 46, r: 8, t: 8, b: 20 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / exp;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
}

function fmtTime(sec: number, span: number): string {
  const d = new Date(sec * 1000);
  return span > 2 * 86400
    ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** Line/area chart for PVE rrddata. Plain SVG: no chart library, scales with its container. */
export default function MetricChart({
  title,
  times,
  series,
  format,
  fixedMax,
  loading,
}: {
  title: string;
  times: number[];
  series: ChartSeries[];
  /** Formats a y value for axis ticks and the hover readout. */
  format: (v: number) => string;
  /** Pin the y axis top (e.g. total memory). Otherwise it autoscales. */
  fixedMax?: number;
  /** True while the first fetch for this period is in flight. */
  loading?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const n = times.length;
  const allValues = series.flatMap((s) => s.values).filter((v): v is number => v != null && Number.isFinite(v));
  const max = fixedMax && fixedMax > 0 ? fixedMax : niceMax(allValues.length ? Math.max(...allValues) * 1.05 : 1);
  const t0 = n ? times[0] : 0;
  const t1 = n ? times[n - 1] : 1;
  const span = Math.max(t1 - t0, 1);
  const x = (t: number) => PAD.l + ((t - t0) / span) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - Math.min(v, max) / max) * (H - PAD.t - PAD.b);

  function path(s: ChartSeries, closed: boolean): string {
    let d = "";
    let run: [number, number][] = [];
    const flush = () => {
      if (run.length > 0) {
        d += "M" + run.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join("L");
        if (closed) {
          const base = (H - PAD.b).toFixed(1);
          d += `L${run[run.length - 1][0].toFixed(1)},${base}L${run[0][0].toFixed(1)},${base}Z`;
        }
      }
      run = [];
    };
    s.values.forEach((v, i) => {
      if (v == null || !Number.isFinite(v)) flush();
      else run.push([x(times[i]), y(v)]);
    });
    flush();
    return d;
  }

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const xTicks = n ? [0, 0.25, 0.5, 0.75, 1].map((f) => t0 + f * span) : [];

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    if (!n) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const t = t0 + ((px - PAD.l) / (W - PAD.l - PAD.r)) * span;
    let best = 0;
    for (let i = 1; i < n; i++) if (Math.abs(times[i] - t) < Math.abs(times[best] - t)) best = i;
    setHover(best);
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted">{title}</div>
        <div className="flex gap-3 text-xs text-muted">
          {series.map((s) => (
            <span key={s.label} className="inline-flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      </div>
      {n === 0 ? (
        <div className="text-sm text-muted py-8 text-center">{loading ? "Loading…" : "No data for this period."}</div>
      ) : (
        <div className="relative">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label={title}>
            {ticks.map((tv) => (
              <g key={tv}>
                <line x1={PAD.l} x2={W - PAD.r} y1={y(tv)} y2={y(tv)} stroke="rgb(var(--c-border))" strokeWidth="1" />
                <text x={PAD.l - 4} y={y(tv) + 3} textAnchor="end" fontSize="9" fill="rgb(var(--c-muted))">
                  {format(tv)}
                </text>
              </g>
            ))}
            {xTicks.map((tt, i) => (
              <text key={i} x={x(tt)} y={H - 6} textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"} fontSize="9" fill="rgb(var(--c-muted))">
                {fmtTime(tt, span)}
              </text>
            ))}
            {series.map((s) => (
              <g key={s.label}>
                {s.area && <path d={path(s, true)} fill={s.color} fillOpacity="0.18" stroke="none" />}
                <path d={path(s, false)} fill="none" stroke={s.color} strokeWidth="1.5" strokeLinejoin="round" />
              </g>
            ))}
            {hover != null && <line x1={x(times[hover])} x2={x(times[hover])} y1={PAD.t} y2={H - PAD.b} stroke="rgb(var(--c-muted))" strokeWidth="1" strokeDasharray="3 3" />}
          </svg>
          {hover != null && (
            <div className="absolute top-0 right-2 bg-surface2 border border-border rounded px-2 py-1 text-xs pointer-events-none">
              <div className="text-muted">{new Date(times[hover] * 1000).toLocaleString()}</div>
              {series.map((s) => (
                <div key={s.label} style={{ color: s.color }}>
                  {s.label}: {s.values[hover] == null ? "—" : format(s.values[hover] as number)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
