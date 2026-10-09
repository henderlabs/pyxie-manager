"use client";

import { useState } from "react";
import type { Operation } from "@/lib/api";
import { colorForPct } from "@/components/Gauges";
import { formatBytes } from "@/lib/format";

type ProjectedNode = { node_id: string; name: string; mem_total_bytes: number; before_pct: number; after_pct: number };
type PlanLine = {
  workload_id: string; vmid: number; name: string | null; memory_bytes?: number | null;
  source_node_id?: string; source_node?: string; destination_node_id: string; destination_node: string;
  transport?: string; pinned_node?: string | null;
};
type LeftAlone = { workload_id: string; vmid: number; name: string | null; node: string | null; reason: "pinned" | "do_not_move" | "recent" | "settled" };
type BlockedMove = {
  workload_id: string; vmid: number; name: string | null; memory_bytes?: number | null;
  source_node: string; blocked_node: string; blocking_reasons: string[]; improvement: number;
  planned_instead: string | null;
  kind?: "no_gain";
};

// How many refused moves show before the "Show all" button: a big cluster refuses dozens of moves and the plan
// itself is what matters, so the list stays short unless asked.
const REFUSED_SHOWN = 4;
const LEFT_ALONE_SHOWN = 6;

const gb = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(bytes >= 100 * 1024 ** 3 ? 0 : 1)} GB`;
const name = (w: { name: string | null; vmid: number }) => w.name || `vmid ${w.vmid}`;
const kindOf = (b: BlockedMove) => (b.kind === "no_gain" ? "no_gain" : b.planned_instead ? "redirected" : "blocked");

/** Memory by node, today vs. after the plan, with the planned moves and a short, expandable list of refused ones.
 *  "After" is recomputed here from the plan as it stands, so changing a destination or setting a line to
 *  "Don't move" in the plan card below moves the bars at once. */
export default function BalanceProjection({ op }: { op: Operation }) {
  const [showAll, setShowAll] = useState(false);
  const r = (op.dry_run_result || {}) as Record<string, unknown>;
  const nodes = (r.projected_memory as ProjectedNode[] | undefined) || [];
  const plan = (r.migrate_plan as PlanLine[] | undefined) || [];
  const blocked = (r.blocked_moves as BlockedMove[] | undefined) || [];
  const leftAlone = (r.left_alone as LeftAlone[] | undefined) || [];
  const leftAloneTotal = (r.left_alone_total as number | undefined) ?? leftAlone.length;
  const blockedTotal = (r.blocked_moves_total as number | undefined) ?? blocked.length;
  if (nodes.length === 0 && plan.length === 0 && blocked.length === 0) return null;

  const moves = plan.filter((p) => p.transport !== "skip");
  const delta: Record<string, number> = {};
  for (const m of moves) {
    const mem = m.memory_bytes || 0;
    if (m.source_node_id) delta[m.source_node_id] = (delta[m.source_node_id] || 0) - mem;
    delta[m.destination_node_id] = (delta[m.destination_node_id] || 0) + mem;
  }
  const rows = nodes.map((n) => {
    const used = (n.mem_total_bytes * n.before_pct) / 100;
    const afterUsed = Math.min(Math.max(used + (delta[n.node_id] || 0), 0), n.mem_total_bytes);
    return {
      ...n, used, afterUsed, after_pct: (afterUsed / n.mem_total_bytes) * 100,
      leaving: moves.filter((m) => m.source_node_id === n.node_id),
      arriving: moves.filter((m) => m.destination_node_id === n.node_id),
    };
  });

  const dense = rows.length > 6;
  const counts = { no_gain: 0, redirected: 0, blocked: 0 };
  for (const b of blocked) counts[kindOf(b)] += 1;
  const shown = showAll ? blocked : blocked.slice(0, REFUSED_SHOWN);

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-3 items-start">
      <div className="border border-border rounded-lg p-5 bg-surface">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-4">Memory by node: now → after plan</div>
        {/* Taller rows and bigger bars, but the card is only as wide as its neighbour. A cluster with many hosts gets
            tighter rows and, past eight, a scroll area, so this card never runs away from the planned-moves card. */}
        <div className={`${dense ? "space-y-3" : "space-y-6"} ${rows.length > 8 ? "max-h-[36rem] overflow-y-auto pr-2" : ""}`}>
          {rows.map((n) => (
            <div key={n.node_id}>
              <div className="flex items-baseline justify-between gap-3 mb-1.5">
                <div className="text-base font-medium text-text truncate" title={n.name}>{n.name}</div>
                <div className={`${dense ? "text-base" : "text-lg"} font-semibold text-text tabular-nums`}>
                  {Math.round(n.before_pct)}% <span className="text-muted">→</span> {Math.round(n.after_pct)}%
                </div>
              </div>
              <div className="space-y-1.5">
                <div className={`${dense ? "h-2" : "h-3"} rounded bg-border overflow-hidden`} title={`Today: ${Math.round(n.before_pct)}%`}>
                  <div className="h-full rounded bg-muted/60" style={{ width: `${n.before_pct}%` }} />
                </div>
                <div className={`${dense ? "h-2" : "h-3"} rounded bg-border overflow-hidden`} title={`After the plan: ${Math.round(n.after_pct)}%`}>
                  <div className="h-full rounded" style={{ width: `${n.after_pct}%`, background: colorForPct(n.after_pct) }} />
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-0.5 mt-1.5 text-xs text-muted">
                <span className="tabular-nums">{gb(n.used)} → {gb(n.afterUsed)} of {gb(n.mem_total_bytes)}</span>
                {(n.leaving.length > 0 || n.arriving.length > 0) && (
                  <span>
                    {n.leaving.length > 0 && <span className="text-good">− {n.leaving.map((m) => `${m.name || "vmid " + m.vmid} (${gb(m.memory_bytes || 0)})`).join(", ")}</span>}
                    {n.leaving.length > 0 && n.arriving.length > 0 && " · "}
                    {n.arriving.length > 0 && <span className="text-accent">+ {n.arriving.map((m) => `${m.name || "vmid " + m.vmid} (${gb(m.memory_bytes || 0)})`).join(", ")}</span>}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
        <p className="text-xs text-muted mt-5">
          Grey bar: today. Colored bar: after the plan (green below 75%, amber from 75%, red from 90%). A projection from each guest&apos;s allocated memory, not a measurement.
        </p>
      </div>

      <div className="border border-border rounded-lg p-4 bg-surface">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-3">
          {moves.length > 0 ? `Planned moves (${moves.length})` : "No moves planned"}
        </div>
        {moves.length > 0 && (
          <ul className="divide-y divide-border text-sm mb-3">
            {moves.map((m) => (
              <li key={m.workload_id} className="flex items-center justify-between gap-3 py-2">
                <span className="min-w-0">
                  <span className="text-text">{name(m)}</span>
                  <span className="text-muted">: {m.source_node || "?"} → {m.destination_node}{m.memory_bytes ? ` (${formatBytes(m.memory_bytes)})` : ""}</span>
                  {m.pinned_node && (
                    <span className="ml-2 px-1.5 py-0.5 rounded text-xs bg-accent/15 text-accent" title="Pinned host: a soft pin set on the workload page">
                      {m.pinned_node === m.destination_node ? "moving to its pin" : `pinned to ${m.pinned_node}`}
                    </span>
                  )}
                </span>
                <span className="shrink-0 px-2 py-0.5 rounded text-xs font-medium bg-good/15 text-good">Passed</span>
              </li>
            ))}
          </ul>
        )}

        {blockedTotal > 0 && (
          <div className="border-t border-border pt-3">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
              <div className="text-sm text-text">
                {blockedTotal} other move{blockedTotal === 1 ? "" : "s"} considered, not made
              </div>
              <div className="flex flex-wrap gap-1.5 text-xs">
                {counts.no_gain > 0 && <span className="px-2 py-0.5 rounded bg-muted/15 text-muted">{counts.no_gain} no gain</span>}
                {counts.blocked > 0 && <span className="px-2 py-0.5 rounded bg-bad/15 text-bad">{counts.blocked} blocked</span>}
                {counts.redirected > 0 && <span className="px-2 py-0.5 rounded bg-warn/15 text-warn">{counts.redirected} redirected</span>}
              </div>
            </div>
            <ul className={`divide-y divide-border text-sm ${showAll ? "max-h-[26rem] overflow-y-auto pr-1" : ""}`}>
              {shown.map((b, i) => (
                <li key={`${b.workload_id}-${i}`} className="py-2">
                  <div className="flex items-center justify-between gap-3">
                    <span className="min-w-0">
                      <span className="text-text">{name(b)}</span>
                      <span className="text-muted">: {b.source_node} → {b.blocked_node}{b.memory_bytes ? ` (${formatBytes(b.memory_bytes)})` : ""}</span>
                    </span>
                    {kindOf(b) === "no_gain" ? (
                      <span className="shrink-0 px-2 py-0.5 rounded text-xs font-medium bg-muted/15 text-muted">No gain</span>
                    ) : kindOf(b) === "redirected" ? (
                      <span className="shrink-0 px-2 py-0.5 rounded text-xs font-medium bg-warn/15 text-warn">Redirected</span>
                    ) : (
                      <span className="shrink-0 px-2 py-0.5 rounded text-xs font-medium bg-bad/15 text-bad">Blocked</span>
                    )}
                  </div>
                  <p className="text-xs text-muted mt-1">
                    {b.blocking_reasons.map((x) => x.replace(/^BLOCKED:?\s*/i, "").replace(/^./, (c) => c.toUpperCase()).replace(/\.?$/, ".")).join(" ")}
                    {b.planned_instead ? ` Sent to ${b.planned_instead} instead.` : " It stays where it is."}
                  </p>
                </li>
              ))}
            </ul>
            {blocked.length > REFUSED_SHOWN && (
              <button onClick={() => setShowAll((v) => !v)} className="mt-2 text-xs text-accent hover:underline">
                {showAll ? "Show fewer" : `Show all ${blocked.length}`}
              </button>
            )}
            {showAll && blockedTotal > blocked.length && <div className="text-xs text-muted mt-1">{blockedTotal - blocked.length} more refused moves are not listed (the most promising {blocked.length} are).</div>}
          </div>
        )}

        {moves.length === 0 && blockedTotal === 0 && <div className="text-sm text-muted">Nothing would make memory use more even right now.</div>}

        {leftAlone.length > 0 && (
          <p className="border-t border-border mt-3 pt-3 text-xs text-muted">
            <span className="text-text">Left alone on purpose:</span>{" "}
            {leftAlone.slice(0, LEFT_ALONE_SHOWN).map((l) => `${l.name || "vmid " + l.vmid} (${l.reason === "pinned" ? `pinned to ${l.node}` : l.reason === "settled" ? "moved twice this week, left where it is" : l.reason === "recent" ? "moved recently" : "do not move"})`).join(", ")}
            {leftAloneTotal > LEFT_ALONE_SHOWN ? `, and ${leftAloneTotal - LEFT_ALONE_SHOWN} more` : ""}.
          </p>
        )}
      </div>
    </div>
  );
}
