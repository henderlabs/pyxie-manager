"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { Node, Operation, StorageItem } from "@/lib/api";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { isInFlight } from "@/lib/operationStatus";
import { useMe } from "@/lib/useMe";

type RecommendCandidate = {
  node_id: string;
  node_name: string;
  blocked: boolean;
  blocking_reasons: string[];
  score: number;
  reasons: string[];
  recommended_storage: { id: string; name: string; scope: string; reason: string } | null;
};

/** Quick single-VM "Migrate" action right on the Workloads table row --
 * same dry-run -> awaiting_approval -> approve -> execute flow as the
 * Maintenance page's "Move to Another Node" (WorkloadLifecycleForm.tsx),
 * same recommend-prefilled node/storage picker, just scoped to one row
 * instead of a page-wide form. No new backend endpoints -- reuses
 * /api/operations/vm-migrations/{recommend,dry-run} and the same
 * approve/dismiss/poll sequence as ApplyRightsizingForm's Resize cell. */
export default function MigrateWorkloadAction({
  workloadId,
  workloadName,
  clusterId,
  currentNodeId,
  nodes,
  storage,
}: {
  workloadId: string;
  workloadName: string;
  clusterId: string;
  currentNodeId: string;
  nodes: Node[];
  storage: StorageItem[];
}) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [destNodeId, setDestNodeId] = useState("");
  const [destStorageId, setDestStorageId] = useState("");
  const [recommendation, setRecommendation] = useState<RecommendCandidate[] | null>(null);
  const [recommendLoading, setRecommendLoading] = useState(false);
  const [op, setOp] = useState<Operation | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  const destinationCandidates = nodes.filter((n) => n.cluster_id === clusterId && n.id !== currentNodeId);
  const storageCandidates = storage.filter((s) => s.scope === "cluster-shared" || (s.scope === "node-local" && s.node_id === destNodeId));
  const topPick = recommendation?.find((c) => !c.blocked) || null;

  useEffect(() => {
    if (!expanded || op) return;
    let cancelled = false;
    setRecommendLoading(true);
    fetch("/api/operations/vm-migrations/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  function collapse() {
    setExpanded(false);
    setDestNodeId("");
    setDestStorageId("");
    setRecommendation(null);
    setOp(null);
    setError(null);
  }

  async function preview() {
    setPending(true);
    setError(null);
    notifyOperationsChanged();
    try {
      const res = await fetch("/api/operations/vm-migrations/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workload_id: workloadId, destination_node_id: destNodeId, destination_storage_id: destStorageId || null }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Preview failed");
        return;
      }
      setOp(data);
      notifyOperationsChanged();
    } finally {
      setPending(false);
    }
  }

  async function dismissAndReset() {
    if (op) {
      fetch(`/api/operations/${op.id}/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dismissed: true }),
      }).catch(() => {});
    }
    notifyOperationsChanged();
    collapse();
    router.refresh();
  }

  async function confirmApprove() {
    if (!op) return;
    const opId = op.id;
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/operations/${opId}/approve`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Approve failed");
        return;
      }
      setOp(data as Operation);
      notifyOperationsChanged();
      // Live migration can run for minutes -- poll in the background to a
      // real terminal status rather than refreshing on a still-mid-flight
      // snapshot (same gap ApplyRightsizingForm's confirmApply already
      // learned to avoid for resize).
      let latest: Operation = data;
      while (isInFlight(latest.status)) {
        await new Promise((r) => setTimeout(r, 2000));
        const opRes = await fetch(`/api/operations/${opId}`);
        if (!opRes.ok) break;
        latest = await opRes.json();
        setOp(latest);
      }
      notifyOperationsChanged();
      router.refresh();
    } finally {
      setPending(false);
    }
  }

  if (!expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className="px-2 py-1 rounded text-xs font-medium bg-ink text-white border border-proxmox hover:bg-proxmox/10"
      >
        Migrate…
      </button>
    );
  }

  return (
    <div className="mt-2 border border-border rounded p-3 bg-surface2/40 text-xs w-full max-w-sm">
      {!op ? (
        <>
          <div className="mb-2">
            <label className="block text-muted mb-1">Destination node</label>
            <select
              className="w-full px-1.5 py-1 rounded bg-surface border border-border text-text"
              value={destNodeId}
              onChange={(e) => {
                setDestNodeId(e.target.value);
                setDestStorageId("");
              }}
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
          <div className="mb-2">
            <label className="block text-muted mb-1">Destination storage</label>
            <select
              className="w-full px-1.5 py-1 rounded bg-surface border border-border text-text"
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
          {recommendLoading && <div className="text-muted mb-2">Ranking destinations…</div>}
          {topPick && (
            <div className="text-muted mb-2">
              <span className="text-accent">Recommended:</span> {topPick.node_name} (score {topPick.score})
              {topPick.recommended_storage && <> · storage: {topPick.recommended_storage.name}</>}
            </div>
          )}
          {error && <div className="text-bad mb-2">{error}</div>}
          <div className="flex gap-2">
            <button
              disabled={!destNodeId || pending}
              onClick={preview}
              className="px-2 py-1 rounded bg-ink text-white border border-proxmox hover:bg-proxmox/10 disabled:opacity-50"
            >
              {pending ? "Checking…" : "Preview"}
            </button>
            <button onClick={collapse} className="text-muted hover:text-text">
              cancel
            </button>
          </div>
        </>
      ) : op.status === "blocked" ? (
        <div>
          <div className="text-bad mb-2">Blocked: {(op.dry_run_result?.reasons || []).join("; ") || "see reasons"}</div>
          <button onClick={dismissAndReset} className="text-muted hover:text-text">
            back
          </button>
        </div>
      ) : isInFlight(op.status) ? (
        <div>
          <div className="text-text mb-2">
            {op.stage || op.status}
            {op.progress?.pct != null && <span className="text-muted"> — {Math.round(op.progress.pct)}%</span>}
          </div>
          <div className="text-muted">Migrating {workloadName}… this can take a while for a large disk.</div>
        </div>
      ) : op.status === "completed" ? (
        <div className="text-good">Migration completed and independently verified.</div>
      ) : op.status === "failed" ? (
        <div>
          <div className="text-bad mb-2">{op.error || "Migration failed"}</div>
          <button onClick={dismissAndReset} className="text-muted hover:text-text">
            close
          </button>
        </div>
      ) : (
        <div>
          <div className="text-text mb-2">
            {op.dry_run_result?.source_node} → {op.dry_run_result?.target_node}
            {op.dry_run_result?.target_storage && <> · storage: {op.dry_run_result.target_storage}</>}
          </div>
          {(op.dry_run_result?.reasons?.length ?? 0) > 0 && (
            <ul className="text-muted mb-2 list-disc list-inside">
              {op.dry_run_result!.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
          {error && <div className="text-bad mb-2">{error}</div>}
          <div className="flex gap-2">
            <button
              disabled={pending}
              onClick={confirmApprove}
              className="px-2 py-1 rounded bg-ink text-white border border-warn hover:bg-warn/10 disabled:opacity-50"
            >
              {pending ? "Starting…" : "Confirm & Migrate"}
            </button>
            <button onClick={dismissAndReset} className="text-muted hover:text-text">
              cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
