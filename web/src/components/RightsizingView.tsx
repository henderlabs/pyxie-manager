"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Node, Recommendation, RightsizingAssessment } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import RecommendationsList from "@/components/RecommendationsList";
import RightsizingTable from "@/components/tables/RightsizingTable";
import RecomputeRightsizingButton from "@/components/RecomputeRightsizingButton";
import { useMe } from "@/lib/useMe";
import { notifyBadgesChanged } from "@/lib/operationsBus";

export type RightsizingTab = "all" | "open" | "acknowledged" | "dismissed" | "resolved";

/** Rightsizing with Acknowledge / Dismiss, same behaviour as Health: acknowledged are kept but muted and not counted,
 * dismissed are hidden until the suggestion changes or clears and comes back. The All tab (default) lists every
 * workload and every suggestion in any state; the other tabs list only workloads with a suggestion in that state. */
export default function RightsizingView({
  tab, rightsizing, nodes, recommendations, resolved, computedAt,
}: {
  tab: RightsizingTab;
  rightsizing: RightsizingAssessment[];
  nodes: Node[];
  recommendations: Recommendation[]; // every non-resolved rightsizing recommendation
  resolved: Recommendation[];
  computedAt: string | null;
}) {
  const router = useRouter();
  const me = useMe();
  const canTriage = me?.is_admin === true;
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const norm = (s: string) => (s === "snoozed" ? "dismissed" : s);
  const recByWorkload = new Map(recommendations.filter((r) => r.object_id).map((r) => [r.object_id as string, r]));
  const inTab = (state: string | undefined) => (tab === "all" ? true : state !== undefined && norm(state) === tab);
  const rows = tab === "resolved" ? [] : rightsizing.filter((a) => inTab(recByWorkload.get(a.workload_id)?.lifecycle_state));
  const cards = tab === "resolved" ? resolved : recommendations.filter((r) => inTab(r.lifecycle_state));
  const rowRecIds = rows.map((a) => recByWorkload.get(a.workload_id)?.id).filter((x): x is string => !!x);

  async function act(ids: string[], action: "acknowledge" | "dismiss" | "reopen") {
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/recommendations/triage", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, action }) });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      setSelected(new Set());
      notifyBadgesChanged();
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const btn = "px-2 py-1 rounded bg-surface2 border border-border text-xs hover:bg-surface2/70";

  const emptyCards: Record<RightsizingTab, string> = {
    all: "No rightsizing suggestions. Either everything looks fine, or there isn't enough history yet -- see the table above.",
    open: "No open rightsizing suggestions.",
    acknowledged: "No acknowledged suggestions.",
    dismissed: "No dismissed suggestions.",
    resolved: "No resolved suggestions.",
  };

  return (
    <>
      {error && <div className="mb-2 text-xs text-danger">{error}</div>}
      {tab !== "resolved" && (
        <Card className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <CardTitle>Rightsizing -- per-workload observation status</CardTitle>
            <RecomputeRightsizingButton computedAt={computedAt} />
          </div>
          {canTriage && selected.size > 0 && (
            <div className="flex items-center gap-2 mb-2 px-3 py-2 rounded bg-accent/10 border border-accent text-sm">
              <span>{selected.size} selected</span>
              {(tab === "all" || tab === "open") && <button disabled={pending} className={`${btn} text-text`} onClick={() => act([...selected], "acknowledge")}>Acknowledge</button>}
              {tab !== "dismissed" && <button disabled={pending} className={`${btn} text-muted`} onClick={() => act([...selected], "dismiss")}>Dismiss</button>}
              {tab !== "open" && <button disabled={pending} className={`${btn} text-text`} onClick={() => act([...selected], "reopen")}>{tab === "acknowledged" ? "Un-acknowledge" : tab === "dismissed" ? "Restore" : "Reopen"}</button>}
            </div>
          )}
          {canTriage && rowRecIds.length > 1 && (
            <label className="flex items-center gap-2 pb-2 text-xs text-muted">
              <input type="checkbox" checked={rowRecIds.every((id) => selected.has(id))} onChange={() => setSelected(rowRecIds.every((id) => selected.has(id)) ? new Set() : new Set(rowRecIds))} />
              Select all with a suggestion
            </label>
          )}
          <RightsizingTable
            key={tab}
            rightsizing={rows}
            nodes={nodes}
            recommendations={recommendations}
            emptyMessage={tab === "all" ? "No workloads discovered yet." : `No ${tab} suggestions.`}
            triage={{ canTriage, pending, selected, toggle, act }}
          />
        </Card>
      )}
      <Card>
        <RecommendationsList recommendations={cards} emptyMessage={emptyCards[tab]} />
      </Card>
    </>
  );
}
