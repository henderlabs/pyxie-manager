"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { isInFlight } from "@/lib/operationStatus";
import { useMe } from "@/lib/useMe";

type DryRunOperation = {
  id: string;
  status: string;
  dry_run_result?: {
    source_node?: string;
    target_node?: string;
    target_storage?: string | null;
    reasons?: string[];
  };
};

export default function ApplyPlacementForm({
  workloadId,
  workloadName,
  currentNode,
  destinationNodeId,
  destinationNode,
  destinationStorageId,
  recommendationId,
  onApplied,
}: {
  workloadId: string;
  workloadName: string;
  currentNode: string;
  destinationNodeId: string;
  destinationNode: string;
  /** Set when the workload's storage preference calls for relocating its
   * disk as part of this move (computed server-side via
   * recommend_storage_for_candidate() -- the same logic every other
   * migration path already uses). Omitted/undefined means no relocation
   * is needed; the dry-run result below still shows whatever PVE actually
   * decides. */
  destinationStorageId?: string;
  /** When set, this recommendation card is resolved the moment the move
   * completes -- otherwise it lingers in the list until the next
   * background scan (up to a minute later) notices the workload already
   * moved. A real gap found live: an applied move stayed listed as a
   * proposed recommendation until that next scan caught up. */
  recommendationId?: string;
  /** For the Maintenance page's on-demand Balance Load list, which isn't
   * a stored Recommendation at all -- just this component's parent's own
   * local proposal array. Called instead of/alongside recommendationId so
   * that list can drop the applied item itself. */
  onApplied?: () => void;
}) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [dryRun, setDryRun] = useState<DryRunOperation | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Live migration of a disk that also needs relocating has to block-mirror
  // it WHILE the guest keeps writing -- markedly slower than copying it
  // once with the guest stopped (live migration can be too costly
  // time-wise with node-local storage). Only relevant when this
  // move also relocates storage; a plain compute-only move has no disk to
  // copy either way, so the choice wouldn't do anything.
  const [transport, setTransport] = useState<"live" | "offline">("live");
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  async function preview() {
    setExpanded(true);
    setPending(true);
    setError(null);
    notifyOperationsChanged();
    try {
      const res = await fetch("/api/operations/vm-migrations/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workload_id: workloadId,
          destination_node_id: destinationNodeId,
          destination_storage_id: destinationStorageId ?? null,
          transport,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Preview failed");
        return;
      }
      setDryRun(data);
      notifyOperationsChanged();
    } finally {
      setPending(false);
    }
  }

  async function dismissAndReset() {
    if (dryRun) {
      fetch(`/api/operations/${dryRun.id}/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dismissed: true }),
      }).catch(() => {});
    }
    setDryRun(null);
    setExpanded(false);
    notifyOperationsChanged();
    router.refresh();
  }

  async function confirmApply() {
    if (!dryRun) return;
    const opId = dryRun.id;
    setPending(true);
    try {
      const res = await fetch(`/api/operations/${opId}/approve`, { method: "POST" });
      if (!res.ok) return;
      notifyOperationsChanged();
      onApplied?.();
      setDryRun(null);
      setExpanded(false);
      // A live migration isn't instant, and one that also relocates
      // storage can take a long time (disk-transfer-bound, up to hours) --
      // router.refresh() right after approval used to capture a
      // still-mid-flight snapshot. Poll to an actual terminal status
      // before refreshing, in the background rather than blocking this
      // button, since this component may already be gone by then (the
      // recommendation resolving, or onApplied() removing it from a
      // Balance Load list) -- same gap as the rightsizing form.
      let status: string = (await res.json()).status;
      while (isInFlight(status)) {
        await new Promise((r) => setTimeout(r, 3000));
        const opRes = await fetch(`/api/operations/${opId}`);
        if (!opRes.ok) break;
        status = (await opRes.json()).status;
      }
      if (recommendationId) {
        fetch(`/api/recommendations/${recommendationId}/lifecycle`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lifecycle_state: "resolved" }),
        }).catch(() => {});
      }
      notifyOperationsChanged();
      router.refresh();
    } finally {
      setPending(false);
    }
  }

  if (!expanded) {
    return (
      <div className="inline-flex items-center gap-1.5">
        {destinationStorageId && (
          <select
            value={transport}
            onChange={(e) => setTransport(e.target.value as "live" | "offline")}
            title="Live keeps the guest running throughout (block-mirrors the disk while it's still being written to). Offline shuts it down first, copies the disk once, then powers it back on -- usually faster for a storage relocation, at the cost of downtime for the copy."
            className="bg-surface2 border border-border rounded px-1 py-1 text-xs"
          >
            <option value="live">Live</option>
            <option value="offline">Shutdown + migrate + power on</option>
          </select>
        )}
        <button
          onClick={preview}
          disabled={pending}
          className="px-2 py-1 rounded text-xs font-medium bg-black text-white border border-proxmox hover:bg-proxmox/10 disabled:opacity-50"
        >
          {pending ? "Checking…" : "Apply…"}
        </button>
      </div>
    );
  }

  return (
    <div className="mt-2 border border-border rounded p-3 bg-surface2/40 text-xs w-full max-w-sm">
      <div className="text-text mb-2">
        {workloadName}: <span className="text-muted">{currentNode}</span> → <span className="text-text font-medium">{destinationNode}</span>
      </div>
      <div className="text-muted mb-2">
        {transport === "offline"
          ? "Offline migration -- guest is shut down, disk copied once, then powered back on."
          : "Live migration -- eligible workloads see no downtime."}{" "}
        {destinationStorageId
          ? "This move also relocates storage to match this workload's storage preference -- disk-transfer-bound, takes noticeably longer than a compute-only move."
          : "No storage relocation needed for this move."}
      </div>

      {error && <div className="text-bad mb-2">{error}</div>}

      {!dryRun ? (
        <div className="text-muted">Checking…</div>
      ) : dryRun.status === "blocked" ? (
        <div>
          <div className="text-bad mb-2">Blocked: {(dryRun.dry_run_result?.reasons || []).join("; ") || "see reasons"}</div>
          <button onClick={dismissAndReset} className="text-muted hover:text-text">
            back
          </button>
        </div>
      ) : (
        <div>
          <div className="text-text mb-2">
            {dryRun.dry_run_result?.source_node} → {dryRun.dry_run_result?.target_node}
            {dryRun.dry_run_result?.target_storage && (
              <span className="text-muted"> · storage → {dryRun.dry_run_result.target_storage}</span>
            )}
          </div>
          <div className="flex gap-2">
            <button
              disabled={pending}
              onClick={confirmApply}
              className="px-2 py-1 rounded bg-black text-white border border-warn hover:bg-warn/10 disabled:opacity-50"
            >
              {pending ? "Migrating…" : "Confirm & Migrate"}
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
