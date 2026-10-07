"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Node, Operation, StorageItem, Workload } from "@/lib/api";
import OperationCard from "@/components/OperationCard";
import WorkloadSearchSelect from "@/components/WorkloadSearchSelect";
import { Table, type Column } from "@/components/Table";
import { Meter } from "@/components/Gauges";
import { useOperationPolling } from "@/lib/useOperationPolling";
import { notifyOperationsChanged, onOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type WorkloadMetric = { cpu_pct?: number; mem_pct?: number; mem_source?: "guest" | "host" };
type WorkloadStorage = { name: string; scope: string | null };

type RecommendCandidate = {
  node_id: string;
  node_name: string;
  blocked: boolean;
  blocking_reasons: string[];
  score: number;
  reasons: string[];
  recommended_storage: { id: string; name: string; scope: string; reason: string } | null;
};

export default function WorkloadLifecycleForm({
  workloads: initialWorkloads,
  nodes,
  storage,
  metrics: initialMetrics,
  currentStorageByWorkload: initialCurrentStorage,
  selectedNodeIds,
  initialWorkloadId,
}: {
  workloads: Workload[];
  nodes: Node[];
  storage: StorageItem[];
  metrics: Record<string, WorkloadMetric>;
  currentStorageByWorkload: Record<string, WorkloadStorage>;
  selectedNodeIds: string[];
  // Arrived here from a VM/CT name link on the Workloads page: clicking
  // a VM/node name anywhere should jump to its "opposing"
  // page with that item already selected, for quick back-and-forth
  // between the two pages' different per-item actions). Same pattern as
  // NodeActionsForm's initialNodeId/initialActionKey.
  initialWorkloadId?: string;
}) {
  const [metrics, setMetrics] = useState(initialMetrics);
  const [currentStorageByWorkload, setCurrentStorageByWorkload] = useState(initialCurrentStorage);
  const [workloads, setWorkloads] = useState(initialWorkloads);

  // Same live-refresh pattern as the Nodes panel: metrics are a cheap DB
  // read (poll continuously); current-storage reads each VM's live qemu
  // config over the PVE API, so it only refreshes on the operations bus
  // (right after a move/resize/etc. actually happens here) rather than a
  // constant timer -- these need to be live-updating. workloads itself
  // (which node each one is actually ON) was still the one-time
  // server-rendered prop -- after a migration completed, this panel kept
  // showing the VM on its old node until a full page reload, a real gap
  // found live: two guests stayed listed under their old node well after
  // their Live Migration rows said Completed. Poll it the same way.
  useEffect(() => {
    function refreshMetrics() {
      fetch("/api/workloads/latest-metrics")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => data && setMetrics(data))
        .catch(() => {});
    }
    function refreshStorage() {
      fetch("/api/workloads/current-storage")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => data && setCurrentStorageByWorkload(data))
        .catch(() => {});
    }
    function refreshWorkloads() {
      fetch("/api/workloads")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => Array.isArray(data) && setWorkloads(data))
        .catch(() => {});
    }
    refreshMetrics();
    refreshWorkloads();
    const interval = setInterval(refreshMetrics, 5000);
    // A migration/move can run for minutes with nobody clicking anything
    // in this tab -- workloads needs its own timer, not just the bus (the
    // bus only fires from a user action elsewhere on the page, e.g.
    // approving something; it's not a heartbeat).
    const workloadsInterval = setInterval(refreshWorkloads, 5000);
    const unsubscribe = onOperationsChanged(() => {
      refreshMetrics();
      refreshStorage();
      refreshWorkloads();
    });
    return () => {
      clearInterval(interval);
      clearInterval(workloadsInterval);
      unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [filterByNode, setFilterByNode] = useState(true);
  const [workloadId, setWorkloadId] = useState(initialWorkloadId || "");
  const [action, setAction] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(120);
  const [justification, setJustification] = useState("");
  const [destNodeId, setDestNodeId] = useState("");
  const [destStorageId, setDestStorageId] = useState("");
  const [recommendation, setRecommendation] = useState<RecommendCandidate[] | null>(null);
  const [recommendLoading, setRecommendLoading] = useState(false);
  const [op, setOp] = useOperationPolling(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  // ---- Bulk Migrate ------------------------------------------------------
  // Tick VMs in the table, optionally pick one destination for all of them
  // (blank = best fit per VM), Preview -> one editable line per VM in the
  // same plan card Balance Load uses, approved once. It IS a cluster.rebalance
  // operation underneath, so execution/cancel/resume are the existing ones.
  const [bulkIds, setBulkIds] = useState<Set<string>>(new Set());
  const [bulkDestId, setBulkDestId] = useState("");
  const [bulkOp, setBulkOp] = useOperationPolling(null);
  const [bulkPending, setBulkPending] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  function toggleBulk(id: string) {
    setBulkIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function runBulkPreview() {
    setBulkPending(true);
    setBulkError(null);
    setBulkOp(null);
    try {
      const res = await fetch("/api/operations/cluster-rebalance/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workload_ids: Array.from(bulkIds), destination_node_id: bulkDestId || null }),
      });
      const data = await res.json();
      if (!res.ok) {
        setBulkError(data.error || "Bulk Migrate preview failed");
        return;
      }
      setBulkOp(data as Operation);
      notifyOperationsChanged();
    } catch (e) {
      setBulkError((e as Error).message);
    } finally {
      setBulkPending(false);
    }
  }

  async function approveBulk() {
    if (!bulkOp) return;
    setBulkPending(true);
    setBulkError(null);
    try {
      const res = await fetch(`/api/operations/${bulkOp.id}/approve`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setBulkError(data.error || "Approve failed");
        return;
      }
      setBulkOp(data as Operation);
      setBulkIds(new Set());
      notifyOperationsChanged();
    } finally {
      setBulkPending(false);
    }
  }

  const nodeNameById = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n.name])), [nodes]);

  // is_missing excluded here -- this feeds the "Select a VM..." action
  // picker below, not a visibility list. Unlike the Workloads page (which
  // deliberately still shows missing/deleted guests with an "unknown"
  // badge, since visibility of what USED to exist is the point there),
  // offering a deleted VM as a real action target here has no honest
  // outcome: PVE has nothing to act on -- a real gap found live, two
  // already-deleted guests (is_missing=true) were selectable here.
  const vms = useMemo(() => workloads.filter((w) => w.type === "vm" && !w.is_missing), [workloads]);
  const visibleVms = useMemo(
    () => (filterByNode && selectedNodeIds.length > 0 ? vms.filter((w) => selectedNodeIds.includes(w.node_id)) : vms),
    [vms, filterByNode, selectedNodeIds]
  );

  const bulkDestNodes = useMemo(() => {
    const clusterIds = new Set(vms.filter((w) => bulkIds.has(w.id)).map((w) => w.cluster_id));
    return nodes.filter((n) => clusterIds.has(n.cluster_id));
  }, [vms, bulkIds, nodes]);

  const bulkColumns: Column<Workload>[] = isAdmin
    ? [
        {
          header: "Pick",
          render: (w) => (
            <input
              type="checkbox"
              checked={bulkIds.has(w.id)}
              onChange={() => toggleBulk(w.id)}
              onClick={(e) => e.stopPropagation()}
              aria-label={`Select ${w.name || `vmid ${w.vmid}`} for bulk migrate`}
            />
          ),
        },
      ]
    : [];

  const workload = vms.find((w) => w.id === workloadId) || null;
  const topPick = recommendation?.find((c) => !c.blocked) || null;
  const destinationCandidates = nodes.filter((n) => workload && n.cluster_id === workload.cluster_id && n.id !== workload.node_id);
  const storageCandidates = useMemo(
    () => storage.filter((s) => s.scope === "cluster-shared" || (s.scope === "node-local" && s.node_id === destNodeId)),
    [storage, destNodeId]
  );

  // Only fetch a recommendation when the "Move" action is actually being
  // used -- no point ranking destinations for a shutdown/start/force-stop.
  useEffect(() => {
    if (!workloadId || action !== "move") {
      setRecommendation(null);
      return;
    }
    let cancelled = false;
    setRecommendLoading(true);
    fetch("/api/operations/vm-migrations/recommend", {
      method: "POST",
      body: JSON.stringify({ workload_id: workloadId }),
    })
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        setRecommendation(data.candidates || []);
        if (data.top_pick) {
          setDestNodeId(data.top_pick.node_id);
          setDestStorageId(data.top_pick.recommended_storage?.id || "");
        }
      })
      .finally(() => {
        if (!cancelled) setRecommendLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workloadId, action]);

  function selectWorkload(id: string) {
    setWorkloadId(id);
    setDestNodeId("");
    setDestStorageId("");
    setRecommendation(null);
    setOp(null);
  }

  // Selections are for the move you are about to make, not a standing choice:
  // once the move has gone (approved from this card OR from the Task Panel),
  // clear the ticked rows; once a single-VM action completes, clear the picker
  // and action too. A failed action keeps its selection so it can be retried.
  // The finished/in-flight operation card itself stays on screen.
  useEffect(() => {
    if (bulkOp && !["awaiting_approval", "pending", "dry_run"].includes(bulkOp.status)) {
      setBulkIds(new Set());
      setBulkDestId("");
    }
  }, [bulkOp?.status]);
  useEffect(() => {
    if (op?.status === "completed") {
      setWorkloadId("");
      setAction("");
      setDestNodeId("");
      setDestStorageId("");
      setRecommendation(null);
    }
  }, [op?.status]);

  // Arrived here via a VM name link from the Workloads page -- scroll the
  // now-preselected (and already-highlighted via rowClassName) row into
  // view instead of leaving the user to hunt for it in a scrolling table.
  const tableScrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!initialWorkloadId) return;
    tableScrollRef.current?.querySelector(".pyxie-jump-target")?.scrollIntoView({ behavior: "smooth", block: "center" });
    // Drop ?workload= from the address bar now that it has been applied, so a
    // page refresh starts with nothing selected instead of re-selecting it.
    try {
      const url = new URL(window.location.href);
      if (url.searchParams.has("workload")) {
        url.searchParams.delete("workload");
        window.history.replaceState(null, "", url.toString());
      }
    } catch {
      /* address bar tidy-up only */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runDryRun() {
    setPending(true);
    setError(null);
    setOp(null);
    notifyOperationsChanged();
    try {
      const res = await fetch(
        action === "move" ? "/api/operations/vm-migrations/dry-run" : "/api/operations/workload-lifecycle/dry-run",
        {
          method: "POST",
          body: JSON.stringify(
            action === "move"
              ? { workload_id: workloadId, destination_node_id: destNodeId, destination_storage_id: destStorageId || null }
              : {
                  workload_id: workloadId,
                  action,
                  timeout_seconds: action === "shutdown" ? timeoutSeconds : undefined,
                  justification: action === "force_stop" ? justification : undefined,
                }
          ),
        }
      );
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Dry run failed");
        return;
      }
      setOp(data);
      notifyOperationsChanged();
    } finally {
      setPending(false);
    }
  }

  async function approve() {
    if (!op) return;
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/operations/${op.id}/approve`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Approve failed");
        return;
      }
      setOp(data);
      notifyOperationsChanged();
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <label className="flex items-center gap-2 text-xs text-muted cursor-pointer">
          <input type="checkbox" checked={filterByNode} onChange={(e) => setFilterByNode(e.target.checked)} />
          Filter to selected node(s)
        </label>
        <div className="flex items-center gap-3 text-xs text-muted">
          {isAdmin && (
            <>
              <button
                type="button"
                className="hover:underline"
                onClick={() => setBulkIds(new Set(visibleVms.map((w) => w.id)))}
                title="Tick every VM currently shown in the table (respects the node filter)"
              >
                Select all shown
              </button>
              {bulkIds.size > 0 && (
                <button type="button" className="hover:underline" onClick={() => setBulkIds(new Set())}>
                  Clear ({bulkIds.size})
                </button>
              )}
            </>
          )}
          <span>{visibleVms.length} VM{visibleVms.length === 1 ? "" : "s"}</span>
        </div>
      </div>

      <div className="max-h-[34rem] overflow-y-auto" ref={tableScrollRef}>
        <Table
          rows={visibleVms}
          emptyMessage={filterByNode && selectedNodeIds.length > 0 ? "No VMs on the selected node(s)." : "No VMs."}
          storageKey="maintenance-workloads"
          onRowClick={(w) => selectWorkload(w.id)}
          rowClassName={(w) => (workloadId === w.id ? "bg-accent/10 pyxie-jump-target" : "")}
          columns={[
            ...bulkColumns,
            {
              header: "Name",
              render: (w) => (
                <Link
                  href={`/infrastructure/workloads/${w.id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="text-accent hover:underline"
                  title="Open on the Workloads page"
                >
                  {w.name || `vmid ${w.vmid}`}
                </Link>
              ),
              sortValue: (w) => w.name || `vmid ${w.vmid}`,
            },
            {
              header: "Node",
              render: (w) => (
                <Link
                  href={`/infrastructure/nodes/${w.node_id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="text-accent hover:underline"
                  title="Open on Hosts & Clusters"
                >
                  {nodeNameById[w.node_id] || "?"}
                </Link>
              ),
              sortValue: (w) => nodeNameById[w.node_id],
            },
            { header: "Status", render: (w) => w.status, sortValue: (w) => w.status },
            {
              header: "CPU",
              render: (w) => <Meter value={metrics[w.id]?.cpu_pct ?? null} width={56} />,
              sortValue: (w) => metrics[w.id]?.cpu_pct,
            },
            {
              header: "RAM",
              render: (w) => <Meter value={metrics[w.id]?.mem_pct ?? null} width={56} hostOnly={metrics[w.id]?.mem_source === "host"} />,
              sortValue: (w) => metrics[w.id]?.mem_pct,
            },
            {
              header: "Storage",
              render: (w) => {
                const s = currentStorageByWorkload[w.id];
                return (
                  <span title={s ? `${s.name} (${s.scope === "node-local" ? "local" : s.scope === "cluster-shared" ? "shared" : "unknown"})` : undefined}>
                    {s?.name || "—"}
                  </span>
                );
              },
              sortValue: (w) => currentStorageByWorkload[w.id]?.name,
            },
          ]}
        />
      </div>

      {isAdmin && bulkIds.size > 0 && (
        <div className="flex flex-wrap items-end gap-3 border border-accent/30 rounded p-3">
          <div className="text-sm">
            <span className="font-medium">{bulkIds.size}</span> VM{bulkIds.size === 1 ? "" : "s"} selected for bulk migrate
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Destination</label>
            <select
              className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm min-w-[240px]"
              value={bulkDestId}
              onChange={(e) => setBulkDestId(e.target.value)}
            >
              <option value="">Auto: best fit for each VM</option>
              {bulkDestNodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.name}
                  {n.maintenance_mode ? " (in maintenance -- will be blocked)" : ""}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={runBulkPreview}
            disabled={bulkPending}
            title="Builds one reviewable line per selected VM -- nothing moves until you review and approve it."
            className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
          >
            {bulkPending ? "Planning…" : "Preview Bulk Migrate"}
          </button>
          {bulkError && <div className="w-full text-sm text-bad">{bulkError}</div>}
        </div>
      )}

      {isAdmin && (
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs text-muted mb-1">Workload</label>
          <WorkloadSearchSelect vms={vms} value={workloadId} onChange={selectWorkload} />
        </div>
        <div>
          <label className="block text-xs text-muted mb-1">Action</label>
          <select
            className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm"
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
              setOp(null);
            }}
          >
            <option value="">Select an action…</option>
            <option value="shutdown">Graceful Shutdown</option>
            <option value="start">Start</option>
            <option value="force_stop">Force Stop (higher-risk)</option>
            <option value="move">Move to Another Node</option>
          </select>
        </div>
        {action === "shutdown" && (
          <div>
            <label className="block text-xs text-muted mb-1">Timeout (seconds)</label>
            <input
              type="number"
              min={10}
              className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm w-24"
              value={timeoutSeconds}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
            />
          </div>
        )}
        {action === "force_stop" && (
          <div>
            <label className="block text-xs text-muted mb-1">Justification (required)</label>
            <input
              className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm min-w-[240px]"
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              placeholder="why a forced stop is necessary"
            />
          </div>
        )}
        {action === "move" && (
          <>
            <div>
              <label className="block text-xs text-muted mb-1">Destination node</label>
              <select
                className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm min-w-[160px]"
                value={destNodeId}
                onChange={(e) => {
                  setDestNodeId(e.target.value);
                  setDestStorageId("");
                }}
                disabled={!workload}
              >
                <option value="">Select a node…</option>
                {destinationCandidates.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                    {n.maintenance_mode ? " (in maintenance -- will be blocked)" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Destination storage</label>
              <select
                className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm min-w-[200px]"
                value={destStorageId}
                onChange={(e) => setDestStorageId(e.target.value)}
                disabled={!destNodeId}
              >
                <option value="">Keep current storage (no relocation)</option>
                {storageCandidates.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.scope === "node-local" ? "local" : "shared"})
                  </option>
                ))}
              </select>
            </div>
          </>
        )}
        <button
          onClick={runDryRun}
          disabled={
            !workloadId ||
            !action ||
            pending ||
            (action === "force_stop" && !justification.trim()) ||
            (action === "move" && !destNodeId)
          }
          title="Shows what would happen -- nothing changes until you review the result and approve it."
          className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          Preview
        </button>
        {!workloadId ? (
          <p className="text-xs text-muted w-full">Select a VM above to enable Preview.</p>
        ) : !action ? (
          <p className="text-xs text-muted w-full">Select an action above to enable Preview.</p>
        ) : null}
      </div>
      )}

      {isAdmin && action === "move" && workloadId && recommendLoading && (
        <p className="text-xs text-muted">Ranking destinations (balance, performance/trust tiers, affinity rules)…</p>
      )}
      {action === "move" && topPick && (
        <p className="text-xs text-muted">
          <span className="text-accent">Recommended:</span> {topPick.node_name} (score {topPick.score}) — {topPick.reasons.join(", ")}
          {topPick.recommended_storage && (
            <> · storage: {topPick.recommended_storage.name} ({topPick.recommended_storage.reason})</>
          )}
          . Still fully editable above.
        </p>
      )}
      {action === "move" && recommendation && recommendation.some((c) => c.blocked) && (
        <p className="text-xs text-muted">
          {recommendation.filter((c) => c.blocked).length} node(s) excluded:{" "}
          {recommendation
            .filter((c) => c.blocked)
            .map((c) => `${c.node_name} (${c.blocking_reasons[0] || "blocked"})`)
            .join("; ")}
        </p>
      )}
      {action === "move" && (
        <p className="text-xs text-muted">
          Leaving storage on "Keep current" only moves compute -- a VM already on shared storage stays on shared storage.
          Pick a specific (e.g. local SSD) target to actually relocate its disk(s) as part of the move.
        </p>
      )}
      <p className="text-xs text-muted">
        Force Stop is deliberately never an automatic fallback if a graceful shutdown times out -- it's always its
        own separately-approved action.
      </p>

      {error && <div className="text-sm text-bad">{error}</div>}
      {op && <OperationCard op={op} pending={pending} onApprove={approve} onUpdated={setOp} />}
      {bulkOp && <OperationCard op={bulkOp} pending={bulkPending} onApprove={approveBulk} onUpdated={setBulkOp} />}
    </div>
  );
}
