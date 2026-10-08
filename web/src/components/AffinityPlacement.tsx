"use client";

import { useState } from "react";
import type { Node, Workload } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";

export type PlacementRule = {
  id: string;
  rule_type: string;
  scope_type: string;
  workload_ids: string[] | null;
  tag: string;
  strict: boolean;
  description: string | null;
};

// One color per rule, cycled. Fixed hues (not theme tokens) so a rule keeps its color in light and dark mode.
const PALETTE = ["#f97316", "#3b82f6", "#a855f7", "#14b8a6", "#ec4899", "#eab308", "#22c55e", "#06b6d4"];

export function ruleLabel(r: PlacementRule, byId: Map<string, Workload>): string {
  if (r.description) return r.description;
  const kind = r.rule_type === "keep_apart" ? "Keep apart" : "Keep together";
  if (r.scope_type === "tag_group") return `${kind}: tag ${r.tag}`;
  return `${kind}: ${(r.workload_ids || []).map((id) => byId.get(id)?.name || id).join(" + ")}`;
}

/** Which guests a rule covers: the named pair, or everything carrying its tag. Missing guests never count. */
export function ruleMembers(r: PlacementRule, workloads: Workload[]): Workload[] {
  const live = workloads.filter((w) => !(w as Workload & { is_missing?: boolean }).is_missing);
  if (r.scope_type === "tag_group") return live.filter((w) => (w.tags || []).includes(r.tag));
  const ids = new Set(r.workload_ids || []);
  return live.filter((w) => ids.has(w.id));
}

/** Keep apart is broken when two members share a node; keep together when members sit on more than one node.
 *  Returns the plain-language problem, or null when the rule holds. */
export function ruleViolation(r: PlacementRule, members: Workload[], nodeName: (id: string) => string): string | null {
  if (members.length < 2) return null;
  if (r.rule_type === "keep_apart") {
    const byNode = new Map<string, string[]>();
    for (const m of members) byNode.set(m.node_id, [...(byNode.get(m.node_id) || []), m.name || `vmid ${m.vmid}`]);
    const clash = [...byNode.entries()].filter(([, names]) => names.length > 1);
    if (clash.length === 0) return null;
    return clash.map(([nid, names]) => `${names.join(" and ")} are both on ${nodeName(nid)}`).join("; ");
  }
  const nodes = new Set(members.map((m) => m.node_id));
  if (nodes.size <= 1) return null;
  return `${members.map((m) => `${m.name || "vmid " + m.vmid} on ${nodeName(m.node_id)}`).join(", ")}: not together`;
}

export default function AffinityPlacement({ rules, workloads, nodes }: { rules: PlacementRule[]; workloads: Workload[]; nodes: Node[] }) {
  const [showOthers, setShowOthers] = useState(false);
  const byId = new Map(workloads.map((w) => [w.id, w]));
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const nodeName = (id: string) => nodeById.get(id)?.name || "unknown node";
  const sortedNodes = [...nodes].sort((a, b) => a.name.localeCompare(b.name));

  const info = rules.map((r, i) => {
    const members = ruleMembers(r, workloads);
    return { rule: r, color: PALETTE[i % PALETTE.length], members, problem: ruleViolation(r, members, nodeName) };
  });
  const rulesOf = new Map<string, typeof info>();
  for (const x of info) for (const m of x.members) rulesOf.set(m.id, [...(rulesOf.get(m.id) || []), x]);
  const brokenIds = new Set<string>();
  for (const x of info) if (x.problem) for (const m of x.members) brokenIds.add(m.id);

  const live = workloads.filter((w) => !(w as Workload & { is_missing?: boolean }).is_missing);
  const others = live.filter((w) => !rulesOf.has(w.id));
  const violations = info.filter((x) => x.problem);

  function chip(w: Workload) {
    const rs = rulesOf.get(w.id) || [];
    const color = rs[0]?.color;
    const broken = brokenIds.has(w.id);
    const stopped = w.status !== "running";
    return (
      <span
        key={w.id}
        title={rs.length ? rs.map((x) => ruleLabel(x.rule, byId)).join(" | ") : "No affinity rule"}
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs ${stopped ? "opacity-60" : ""} ${broken ? "ring-2 ring-bad" : ""} ${color ? "text-text" : "text-muted border-border bg-surface2"}`}
        style={color ? { borderColor: color, background: `${color}22` } : undefined}
      >
        {w.name || `vmid ${w.vmid}`}
        {rs.slice(1).map((x) => (
          <span key={x.rule.id} className="w-1.5 h-1.5 rounded-full" style={{ background: x.color }} />
        ))}
        {broken && <span className="text-bad font-semibold">!</span>}
      </span>
    );
  }

  return (
    <Card className="mb-4">
      <CardTitle>Where the guests sit now</CardTitle>
      {rules.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1.5 mb-3 text-xs">
          {info.map((x) => (
            <span key={x.rule.id} className="inline-flex items-center gap-1.5 text-text">
              <span className="w-2.5 h-2.5 rounded-sm" style={{ background: x.color }} />
              {ruleLabel(x.rule, byId)}
              <span className={x.problem ? "text-bad font-medium" : "text-good"}>{x.problem ? "Broken" : x.members.length < 2 ? "Nothing to check" : "Holding"}</span>
            </span>
          ))}
        </div>
      )}
      {violations.length > 0 && (
        <div className="mb-3 rounded border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">
          {violations.map((x) => (
            <div key={x.rule.id}>
              {ruleLabel(x.rule, byId)}: {x.problem}.
            </div>
          ))}
          <div className="text-xs text-muted mt-1">Existing placements aren&apos;t moved automatically. Rules are enforced on every new move; Balance Load and migrations will not make these worse.</div>
        </div>
      )}
      <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(Math.max(sortedNodes.length, 1), 4)}, minmax(0, 1fr))` }}>
        {sortedNodes.map((n) => {
          const here = live.filter((w) => w.node_id === n.id);
          const ruled = here.filter((w) => rulesOf.has(w.id));
          const rest = here.filter((w) => !rulesOf.has(w.id));
          return (
            <div key={n.id} className="border border-border rounded-lg p-3 bg-surface2/40 min-h-[96px]">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">
                {n.name} <span className="font-normal normal-case">· {here.length} guests</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {ruled.map(chip)}
                {showOthers && rest.map(chip)}
              </div>
              {!showOthers && rest.length > 0 && <div className="text-xs text-muted mt-2">+ {rest.length} without a rule</div>}
            </div>
          );
        })}
      </div>
      {others.length > 0 && (
        <button onClick={() => setShowOthers((v) => !v)} className="text-xs text-accent hover:underline mt-3">
          {showOthers ? "Hide guests without a rule" : `Show the ${others.length} guests without a rule`}
        </button>
      )}
      <p className="text-xs text-muted mt-3">
        Colored chips belong to a rule (the color is the rule&apos;s); a small dot marks a guest in more than one rule; red <span className="text-bad">!</span> marks a guest whose rule is currently broken; grey guests have no rule. Migrations, evacuations and Balance Load check these rules first, and again against live state when each move runs.
      </p>
    </Card>
  );
}
