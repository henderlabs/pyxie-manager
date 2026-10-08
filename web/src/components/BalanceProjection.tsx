import type { Operation } from "@/lib/api";
import { colorForPct } from "@/components/Gauges";
import { formatBytes } from "@/lib/format";

type ProjectedNode = { node_id: string; name: string; mem_total_bytes: number; before_pct: number; after_pct: number };
type PlanLine = {
  workload_id: string; vmid: number; name: string | null; memory_bytes?: number | null;
  source_node_id?: string; source_node?: string; destination_node_id: string; destination_node: string;
  transport?: string;
};
type BlockedMove = {
  workload_id: string; vmid: number; name: string | null; memory_bytes?: number | null;
  source_node: string; blocked_node: string; blocking_reasons: string[]; improvement: number;
  planned_instead: string | null;
};

/** Memory by node, today vs. after the plan, with every planned move and every refused one listed.
 *  "After" is recomputed here from the plan as it stands, so changing a destination or setting a line to
 *  "Don't move" in the plan card below moves the bars at once. */
export default function BalanceProjection({ op }: { op: Operation }) {
  const r = (op.dry_run_result || {}) as Record<string, unknown>;
  const nodes = (r.projected_memory as ProjectedNode[] | undefined) || [];
  const plan = (r.migrate_plan as PlanLine[] | undefined) || [];
  const blocked = (r.blocked_moves as BlockedMove[] | undefined) || [];
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
    const after = Math.min(Math.max(used + (delta[n.node_id] || 0), 0) / n.mem_total_bytes * 100, 100);
    return { ...n, after_pct: after };
  });

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-3">
      <div className="border border-border rounded-lg p-4 bg-surface">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-3">Memory by node: now → after plan</div>
        <div className="space-y-3">
          {rows.map((n) => (
            <div key={n.node_id} className="flex items-center gap-3">
              <div className="w-24 shrink-0 text-sm text-text truncate" title={n.name}>{n.name}</div>
              <div className="flex-1 space-y-1">
                <div className="h-2 rounded bg-border overflow-hidden" title={`Today: ${Math.round(n.before_pct)}%`}>
                  <div className="h-full rounded bg-muted/60" style={{ width: `${n.before_pct}%` }} />
                </div>
                <div className="h-2 rounded bg-border overflow-hidden" title={`After the plan: ${Math.round(n.after_pct)}%`}>
                  <div className="h-full rounded" style={{ width: `${n.after_pct}%`, background: colorForPct(n.after_pct) }} />
                </div>
              </div>
              <div className="w-24 shrink-0 text-right text-sm text-text tabular-nums">
                {Math.round(n.before_pct)}% → {Math.round(n.after_pct)}%
              </div>
            </div>
          ))}
        </div>
        <p className="text-xs text-muted mt-3">
          Grey: today. Colored: after the plan (green below 75%, amber from 75%, red from 90%). A projection from each guest&apos;s allocated memory, not a measurement.
        </p>
      </div>

      <div className="border border-border rounded-lg p-4 bg-surface">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-3">Planned moves, each checked on its own</div>
        <ul className="divide-y divide-border text-sm">
          {moves.map((m) => (
            <li key={m.workload_id} className="flex items-center justify-between gap-3 py-2">
              <span className="min-w-0">
                <span className="text-text">{m.name || `vmid ${m.vmid}`}</span>
                <span className="text-muted">: {m.source_node || "?"} → {m.destination_node}{m.memory_bytes ? ` (${formatBytes(m.memory_bytes)})` : ""}</span>
              </span>
              <span className="shrink-0 px-2 py-0.5 rounded text-xs font-medium bg-good/15 text-good">Passed</span>
            </li>
          ))}
          {blocked.map((b) => (
            <li key={b.workload_id} className="py-2">
              <div className="flex items-center justify-between gap-3">
                <span className="min-w-0">
                  <span className="text-text">{b.name || `vmid ${b.vmid}`}</span>
                  <span className="text-muted">: {b.source_node} → {b.blocked_node}{b.memory_bytes ? ` (${formatBytes(b.memory_bytes)})` : ""}</span>
                </span>
                <span className="shrink-0 px-2 py-0.5 rounded text-xs font-medium bg-bad/15 text-bad">Blocked</span>
              </div>
              <p className="text-xs text-muted mt-1">
                {b.blocking_reasons.map((x) => x.replace(/^BLOCKED:?\s*/i, "").replace(/\.?$/, ".")).join(" ")}
                {b.planned_instead ? ` Sent to ${b.planned_instead} instead.` : " It stays where it is."}
              </p>
            </li>
          ))}
          {blockedTotal > blocked.length && <li className="py-2 text-xs text-muted">{blockedTotal - blocked.length} more refused moves not shown.</li>}
          {moves.length === 0 && blocked.length === 0 && <li className="py-2 text-muted">No moves.</li>}
        </ul>
      </div>
    </div>
  );
}
