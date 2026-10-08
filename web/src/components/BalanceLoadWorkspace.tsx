"use client";

import { useEffect, useState } from "react";
import type { Node, Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OperationCard from "@/components/OperationCard";
import BalanceProjection from "@/components/BalanceProjection";
import { MigrateIcon } from "@/components/Icons";
import { notifyOperationsChanged, onOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";
import { isInFlight, isTerminal } from "@/lib/operationStatus";

/** Balance Load on its own page: pick which nodes to take guests off (none = the whole cluster), preview the plan,
 *  approve it. Same dry-run/approve path as every other batch migration; nothing moves until Approve. */
export default function BalanceLoadWorkspace({ nodes, initialNodeId }: { nodes: Node[]; initialNodeId?: string }) {
  const [selected, setSelected] = useState<string[]>(initialNodeId ? [initialNodeId] : []);
  const [ops, setOps] = useState<Operation[]>([]);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function evaluate() {
    setBusy(true);
    setError(null);
    setOps([]);
    try {
      const res = await fetch("/api/operations/cluster-rebalance/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_ids: selected.length > 0 ? selected : null }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Balance Load failed");
      setOps([data as Operation]);
      notifyOperationsChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Poll an operation that is running; refresh one that is waiting when something signals a change.
  useEffect(() => {
    if (!ops.some((o) => isInFlight(o.status))) return;
    const t = setInterval(async () => {
      const updated = await Promise.all(
        ops.map(async (o) => {
          if (!isInFlight(o.status)) return o;
          const res = await fetch(`/api/operations/${o.id}`);
          return res.ok ? ((await res.json()) as Operation) : o;
        })
      );
      setOps(updated);
      if (updated.some((o, i) => o.status !== ops[i]?.status)) notifyOperationsChanged();
    }, 2000);
    return () => clearInterval(t);
  }, [ops]);

  useEffect(() => {
    const waiting = ops.filter((o) => !isTerminal(o.status) && !isInFlight(o.status));
    if (waiting.length === 0) return;
    let cancelled = false;
    async function refreshWaiting() {
      const fresh = await Promise.all(
        waiting.map(async (o) => {
          try {
            const res = await fetch(`/api/operations/${o.id}`);
            return res.ok ? ((await res.json()) as Operation) : o;
          } catch {
            return o;
          }
        })
      );
      if (cancelled || !fresh.some((o, i) => o.status !== waiting[i].status)) return;
      setOps((prev) => prev.map((o) => fresh.find((f) => f.id === o.id) ?? o));
    }
    const unsubscribe = onOperationsChanged(refreshWaiting);
    const t = setInterval(refreshWaiting, 5000);
    return () => {
      cancelled = true;
      unsubscribe();
      clearInterval(t);
    };
  }, [ops]);

  async function approve(op: Operation) {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/operations/${op.id}/approve`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Approve failed");
        return;
      }
      setOps((prev) => prev.map((o) => (o.id === data.id ? data : o)));
      notifyOperationsChanged();
    } finally {
      setPending(false);
    }
  }

  const live = nodes;

  return (
    <div className="space-y-4">
      <Card>
        <CardTitle>Where to take guests from</CardTitle>
        <div className="flex flex-wrap gap-2 mb-3">
          {live.map((n) => {
            const on = selected.includes(n.id);
            return (
              <button
                key={n.id}
                onClick={() => setSelected((p) => (p.includes(n.id) ? p.filter((x) => x !== n.id) : [...p, n.id]))}
                className={`px-3 py-1.5 rounded border text-sm ${on ? "border-accent bg-accent/10 text-text" : "border-border text-muted hover:text-text"}`}
              >
                {n.name}
              </button>
            );
          })}
        </div>
        <p className="text-xs text-muted mb-3">
          {selected.length > 0
            ? "Only guests now on the selected node(s) are considered for moving; they can go to any eligible node."
            : "No node selected: every running VM in the cluster is considered."}{" "}
          Each move is checked against affinity rules, headroom, CPU compatibility and maintenance mode first, and again when it runs.
        </p>
        {isAdmin && (
          <button
            onClick={evaluate}
            disabled={busy}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
          >
            <MigrateIcon className="w-4 h-4" />
            {busy ? "Evaluating…" : "Preview Balance Load"}
          </button>
        )}
      </Card>

      {error && <div className="text-sm text-bad">{error}</div>}
      {ops.map((op) => (
        <div key={op.id}>
          <BalanceProjection op={op} />
          <OperationCard
            op={op}
            pending={pending}
            onApprove={() => approve(op)}
            onUpdated={(updated) => setOps((prev) => prev.map((o) => (o.id === updated.id ? updated : o)))}
          />
        </div>
      ))}
    </div>
  );
}
