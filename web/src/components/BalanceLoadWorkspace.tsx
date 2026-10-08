"use client";

import { useEffect, useState } from "react";
import type { Node, Operation, Workload } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OperationCard from "@/components/OperationCard";
import BalanceProjection from "@/components/BalanceProjection";
import { MigrateIcon, ClusterIcon } from "@/components/Icons";
import { BalanceGauge, computeBalance } from "@/components/ClusterBalance";
import { colorForPct } from "@/components/Gauges";
import { notifyOperationsChanged, onOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";
import { isInFlight, isTerminal } from "@/lib/operationStatus";

/** Balance Load on its own page: pick which nodes to take guests off (none = the whole cluster), preview the plan,
 *  approve it. Same dry-run/approve path as every other batch migration; nothing moves until Approve. */
type Load = { label: "Heavy" | "Even" | "Light" | "Out"; cls: string };

// Relative to the cluster's own average, not an absolute scale: the question here is "who is carrying more than their share".
function loadOf(n: Node, avg: number | null): Load {
  if (n.status !== "online" || n.maintenance_mode || n.mem_usage_pct === null || avg === null) return { label: "Out", cls: "bg-muted/15 text-muted" };
  if (n.mem_usage_pct >= avg + 12) return { label: "Heavy", cls: "bg-bad/15 text-bad" };
  if (n.mem_usage_pct <= avg - 12) return { label: "Light", cls: "bg-accent/15 text-accent" };
  return { label: "Even", cls: "bg-good/15 text-good" };
}

export default function BalanceLoadWorkspace({ nodes, workloads, initialNodeId }: { nodes: Node[]; workloads: Workload[]; initialNodeId?: string }) {
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

  const live = [...nodes].sort((a, b) => a.name.localeCompare(b.name));
  const b = computeBalance(nodes);
  const avg = b ? b.memAvg : null;
  const guests = (id: string) => workloads.filter((w) => w.node_id === id && w.status === "running").length;
  const heavy = live.filter((n) => loadOf(n, avg).label === "Heavy");
  const selectable = live.filter((n) => n.status === "online" && !n.maintenance_mode);
  const previewed = ops.length > 0;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card>
          <CardTitle>Cluster balance</CardTitle>
          <BalanceGauge nodes={nodes} icon={<ClusterIcon />} />
          {b && (
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs mt-3">
              <dt className="text-muted">Memory, cluster average</dt><dd className="text-text text-right tabular-nums">{Math.round(b.memAvg)}%</dd>
              <dt className="text-muted">Busiest / quietest node</dt><dd className="text-text text-right tabular-nums">{Math.round(b.memMax)}% / {Math.round(b.memMin)}%</dd>
              <dt className="text-muted">Nodes counted</dt><dd className="text-text text-right tabular-nums">{b.counted} of {nodes.length}</dd>
              <dt className="text-muted">Running guests</dt><dd className="text-text text-right tabular-nums">{workloads.filter((w) => w.status === "running").length}</dd>
            </dl>
          )}
          <p className="text-xs text-muted mt-3">
            {!b
              ? "Not enough nodes with live data to judge balance."
              : b.score >= 80
              ? "The cluster is already even. A preview will probably find little worth moving."
              : heavy.length > 0
              ? `Suggestion: take guests off ${heavy.map((n) => n.name).join(", ")} (memory ${heavy.map((n) => Math.round(n.mem_usage_pct as number) + "%").join(", ")}, cluster average ${Math.round(b.memAvg)}%).`
              : "Memory is uneven but no single node stands out; preview the whole cluster."}
          </p>
        </Card>
        <Card className="lg:col-span-2">
          <CardTitle>{previewed ? "Where guests would be taken from" : "Node load today"}</CardTitle>
          <div className="space-y-2">
            {live.map((n) => {
              const l = loadOf(n, avg);
              const on = selected.includes(n.id);
              const ok = selectable.some((x) => x.id === n.id);
              const mem = n.mem_usage_pct;
              return (
                <label key={n.id} className={`flex items-center gap-3 rounded border px-3 py-2 ${ok ? "cursor-pointer" : "opacity-50"} ${on ? "border-accent bg-accent/10" : "border-border"}`}>
                  <input type="checkbox" checked={on} disabled={!ok} onChange={() => setSelected((p) => (p.includes(n.id) ? p.filter((x) => x !== n.id) : [...p, n.id]))} className="accent-[rgb(var(--c-accent))]" />
                  <span className="w-20 shrink-0 text-sm font-medium text-text truncate" title={n.name}>{n.name}</span>
                  <span className="flex-1 min-w-[80px]">
                    <span className="block h-2 rounded bg-border overflow-hidden">
                      <span className="block h-full rounded" style={{ width: `${mem ?? 0}%`, background: colorForPct(mem) }} />
                    </span>
                  </span>
                  <span className="w-44 shrink-0 text-xs text-muted tabular-nums">
                    memory {mem === null ? "—" : Math.round(mem) + "%"} · cpu {n.cpu_usage_pct === null ? "—" : Math.round(n.cpu_usage_pct) + "%"} · {guests(n.id)} guests
                  </span>
                  <span className={`w-14 shrink-0 text-center px-2 py-0.5 rounded text-xs font-medium ${l.cls}`}>{l.label}</span>
                </label>
              );
            })}
          </div>
          <div className="flex flex-wrap items-center gap-2 mt-3 text-xs">
            <button onClick={() => setSelected(selectable.map((n) => n.id))} className="px-2 py-1 rounded border border-border text-muted hover:text-text">Select all</button>
            {heavy.length > 0 && (
              <button onClick={() => setSelected(heavy.map((n) => n.id))} className="px-2 py-1 rounded border border-border text-muted hover:text-text">Select heavy nodes</button>
            )}
            <button onClick={() => setSelected([])} className="px-2 py-1 rounded border border-border text-muted hover:text-text">Clear</button>
            <span className="text-muted">
              {selected.length > 0
                ? `Only guests now on the ${selected.length} selected node(s) are considered for moving; they can go to any eligible node.`
                : "Nothing selected: every running VM in the cluster is considered."}
            </span>
          </div>
        </Card>
      </div>

      <Card>
        <CardTitle>How it works</CardTitle>
        <p className="text-sm text-muted mb-3">
          Balance Load scores every running VM against every eligible node and proposes moves that make memory use more even.
          Each move is checked against affinity rules, memory headroom, CPU compatibility, HA and maintenance mode, then
          checked again at the moment it runs. Nothing moves until you approve, and you can change any destination or
          set a line to &ldquo;Don&apos;t move&rdquo;.
        </p>
        {isAdmin && (
          <button
            onClick={evaluate}
            disabled={busy}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
          >
            <MigrateIcon className="w-4 h-4" />
            {busy ? "Evaluating…" : selected.length > 0 ? `Preview Balance Load for ${selected.length} node${selected.length === 1 ? "" : "s"}` : "Preview Balance Load"}
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
