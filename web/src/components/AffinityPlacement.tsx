"use client";

import { useState } from "react";
import Link from "next/link";
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
  color?: string | null;
};

// One color per rule, cycled. Fixed hues (not theme tokens) so a rule keeps its color in light and dark mode.
/** The rule's own color if the operator picked one, else the cycled default. */
export function ruleColor(r: { color?: string | null }, rules: unknown[], i: number): string {
  return r.color || PALETTE[(rules.length - 1 - i) % PALETTE.length];
}

export const PALETTE = ["#f97316", "#3b82f6", "#a855f7", "#14b8a6", "#ec4899", "#eab308", "#22c55e", "#06b6d4"];

export function ruleLabel(r: PlacementRule, byId: Map<string, Workload>): string {
  if (r.description) return r.description;
  const kind = r.rule_type === "keep_apart" ? "Keep apart" : "Keep together";
  if (r.scope_type === "tag_group") return `${kind}: tag ${r.tag}`;
  return `${kind}: ${(r.workload_ids || []).map((id) => byId.get(id)?.name || id).join(" + ")}`;
}

/** The rule in plain words, e.g. "db-01 and db-02 must stay on different nodes". */
export function ruleSentence(r: PlacementRule, byId: Map<string, Workload>): string {
  const apart = r.rule_type === "keep_apart";
  const names = (r.workload_ids || []).map((id) => byId.get(id)?.name || "a missing guest");
  const who = r.scope_type === "tag_group" ? `Guests tagged "${r.tag}"` : names.length === 2 ? `${names[0]} and ${names[1]}` : names.join(", ");
  return `${who} ${r.strict ? "must" : "should"} stay on ${apart ? "different nodes" : "the same node"}${r.strict ? "" : " (a preference, not a hard block)"}`;
}

/** The "must stay on different nodes" half of the sentence, for views that draw the guest names themselves. */
export function ruleTail(r: PlacementRule): string {
  return `${r.strict ? "must" : "should"} stay on ${r.rule_type === "keep_apart" ? "different nodes" : "the same node"}${r.strict ? "" : " (a preference, not a hard block)"}`;
}

/** A guest as a colored pill (the rule's color; grey without a rule), linking to the guest. Shared by the placement card and the rules list. */
export function GuestPill({ w, color, dots = [], flag = null, title }: { w: Workload; color?: string; dots?: string[]; flag?: "broken" | "unmet" | null; title?: string }) {
  const stopped = w.status !== "running";
  return (
    <Link
      href={`/infrastructure/workloads/${w.id}`}
      title={title ?? `Open ${w.name || "guest"}`}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs hover:brightness-125 ${stopped ? "opacity-60" : ""} ${flag === "broken" ? "ring-2 ring-bad" : flag === "unmet" ? "ring-2 ring-warn" : ""} ${color ? "text-text" : "text-muted border-border bg-surface2"}`}
      style={color ? { borderColor: color, background: `${color}22` } : undefined}
    >
      {w.name || `vmid ${w.vmid}`}
      {dots.map((c, i) => (
        <span key={i} className="w-1.5 h-1.5 rounded-full" style={{ background: c }} />
      ))}
      {flag === "broken" && <span className="text-bad font-semibold">!</span>}
      {flag === "unmet" && <span className="text-warn font-semibold">!</span>}
    </Link>
  );
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
  return `they are spread over ${nodes.size} nodes (${members.map((m) => `${m.name || "vmid " + m.vmid} on ${nodeName(m.node_id)}`).join(", ")})`;
}

export function ruleStatus(r: PlacementRule, workloads: Workload[], nodes: Node[]) {
  const nodeName = (id: string) => nodes.find((n) => n.id === id)?.name || "unknown node";
  const members = ruleMembers(r, workloads);
  return { members, problem: ruleViolation(r, members, nodeName), nodeName };
}

export default function AffinityPlacement({ rules, workloads, nodes }: { rules: PlacementRule[]; workloads: Workload[]; nodes: Node[] }) {
  const [showOthers, setShowOthers] = useState(true);
  const byId = new Map(workloads.map((w) => [w.id, w]));
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const nodeName = (id: string) => nodeById.get(id)?.name || "unknown node";
  const sortedNodes = [...nodes].sort((a, b) => a.name.localeCompare(b.name));

  const info = rules.map((r, i) => {
    const members = ruleMembers(r, workloads);
    return { rule: r, color: ruleColor(r, rules, i), members, problem: ruleViolation(r, members, nodeName) };
  });
  const rulesOf = new Map<string, typeof info>();
  for (const x of info) for (const m of x.members) rulesOf.set(m.id, [...(rulesOf.get(m.id) || []), x]);
  const brokenIds = new Set<string>(); // a hard rule is broken
  const unmetIds = new Set<string>(); // only a soft preference is unmet
  for (const x of info) if (x.problem) for (const m of x.members) (x.rule.strict ? brokenIds : unmetIds).add(m.id);

  const live = workloads.filter((w) => !(w as Workload & { is_missing?: boolean }).is_missing);
  const others = live.filter((w) => !rulesOf.has(w.id));
  const violations = info.filter((x) => x.problem);
  const anyHard = violations.some((x) => x.rule.strict);

  function chip(w: Workload) {
    const rs = rulesOf.get(w.id) || [];
    const broken = brokenIds.has(w.id);
    const unmet = !broken && unmetIds.has(w.id);
    return (
      <GuestPill
        key={w.id}
        w={w}
        color={rs[0]?.color}
        dots={rs.slice(1).map((x) => x.color)}
        flag={broken ? "broken" : unmet ? "unmet" : null}
        title={`${rs.length ? rs.map((x) => ruleLabel(x.rule, byId)).join(" | ") : "No affinity rule"} (open ${w.name || "guest"})`}
      />
    );
  }

  return (
    <Card className="mb-4">
      <CardTitle>Where the guests sit now</CardTitle>
      {violations.length > 0 && (
        <div className={`mb-3 rounded border px-3 py-2 text-sm ${anyHard ? "border-bad/40 bg-bad/10 text-bad" : "border-warn/40 bg-warn/10 text-warn"}`}>
          {violations.map((x) => (
            <div key={x.rule.id}>
              {ruleSentence(x.rule, byId)}, but {x.problem}.
            </div>
          ))}
          <div className="text-xs text-muted mt-1">PyXie never moves a guest on its own to fix this. Rules are checked on every new move.</div>
        </div>
      )}
      <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(Math.max(sortedNodes.length, 1), 4)}, minmax(0, 1fr))` }}>
        {sortedNodes.map((n) => {
          const here = live.filter((w) => w.node_id === n.id);
          const ruled = here.filter((w) => rulesOf.has(w.id));
          const rest = here.filter((w) => !rulesOf.has(w.id));
          return (
            <div key={n.id} className="border border-border rounded-lg p-3 bg-surface2/40 min-h-[96px]">
              <div className="text-xs font-semibold tracking-wider text-muted mb-2">
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
          {showOthers ? `Hide the ${others.length} guests without a rule` : `Show the ${others.length} guests without a rule`}
        </button>
      )}
      <p className="text-xs text-muted mt-3">
        Each colored chip belongs to the rule with the same color swatch below; grey guests have no rule; a small dot means a guest is in more than one rule; a red <span className="text-bad">!</span> means its rule is broken right now (an amber <span className="text-warn">!</span> means a soft preference is not met). Click a guest to open it. Migrations, evacuations and Balance Load check these rules first, and again against live state when each move runs.
      </p>
    </Card>
  );
}
