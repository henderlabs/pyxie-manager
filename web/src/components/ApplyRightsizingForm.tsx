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
    current_cores?: number;
    new_cores?: number;
    current_memory_bytes?: number;
    new_memory_bytes?: number;
    will_power_cycle?: boolean;
    reasons?: string[];
  };
};

function bytesToGb(bytes: number | null | undefined): number {
  return bytes ? Math.round((bytes / 1024 ** 3) * 10) / 10 : 0;
}

export default function ApplyRightsizingForm({
  workloadId,
  workloadName,
  currentCores,
  currentMemoryBytes,
  suggestedCores,
  suggestedMemoryBytes,
  recommendationId,
  triggerLabel,
}: {
  workloadId: string;
  workloadName: string;
  currentCores: number | null;
  currentMemoryBytes: number | null;
  suggestedCores: number | null;
  suggestedMemoryBytes: number | null;
  /** Resolved the moment the resize completes, instead of lingering until
   * the next background scan notices the workload already changed. */
  recommendationId?: string;
  /** Overrides the collapsed-state trigger's contents -- e.g. the
   * Rightsizing table renders the suggestion text itself ("4 → 3 vCPU")
   * as the button instead of a generic "Apply…". */
  triggerLabel?: React.ReactNode;
}) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [cores, setCores] = useState(suggestedCores ?? currentCores ?? 1);
  const [memoryGb, setMemoryGb] = useState(bytesToGb(suggestedMemoryBytes ?? currentMemoryBytes));
  const [dryRun, setDryRun] = useState<DryRunOperation | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  async function preview() {
    setPending(true);
    setError(null);
    notifyOperationsChanged();
    try {
      const res = await fetch("/api/operations/workload-resizes/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workload_id: workloadId, new_cores: cores, new_memory_mb: Math.round(memoryGb * 1024) }),
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
      // The preview's dry-run already created a real Operation row --
      // cancelling here must actually dismiss it server-side, not just
      // hide it from this form. Otherwise it lingers in the Task Panel's
      // Awaiting Approval list forever, since nothing else ever points
      // back at it once this form resets to "Apply...".
      fetch(`/api/operations/${dryRun.id}/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dismissed: true }),
      }).catch(() => {});
    }
    setDryRun(null);
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
      setDryRun(null);
      setExpanded(false);
      // A resize powers the VM off, changes cores/RAM, then powers it back
      // on -- it isn't instant. router.refresh() right after approval used
      // to capture a still-mid-flight snapshot, so the table below looked
      // like nothing happened: the suggestion hadn't cleared and CPU/RAM
      // hadn't updated, a real gap found live applying a real resize.
      // Poll to an actual terminal status before refreshing --
      // in the background, not blocking this button, since this component
      // may collapse/unmount as soon as the recommendation resolves.
      let status: string = (await res.json()).status;
      while (isInFlight(status)) {
        await new Promise((r) => setTimeout(r, 2000));
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
      <button
        onClick={() => setExpanded(true)}
        className="px-2 py-1 rounded text-xs font-medium bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 text-left leading-snug"
      >
        {triggerLabel ?? "Apply…"}
      </button>
    );
  }

  return (
    <div className="mt-2 border border-border rounded p-3 bg-surface2/40 text-xs w-full max-w-sm">
      <div className="text-warn font-medium mb-2">
        ⚠ This will power off {workloadName} to apply the change, then power it back on. vCPU/RAM changes aren&apos;t
        hot-pluggable.
      </div>

      <div className="flex items-center gap-2 mb-1.5">
        <label className="text-muted w-20">vCPU</label>
        <input
          type="number"
          min={1}
          value={cores}
          onChange={(e) => setCores(Math.max(1, Number(e.target.value)))}
          disabled={!!dryRun}
          className="w-20 px-1.5 py-1 rounded bg-surface border border-border text-text disabled:opacity-60"
        />
        <span className="text-muted">(current: {currentCores ?? "?"})</span>
      </div>
      <div className="flex items-center gap-2 mb-2">
        <label className="text-muted w-20">Memory (GB)</label>
        <input
          type="number"
          min={0.5}
          step={0.5}
          value={memoryGb}
          onChange={(e) => setMemoryGb(Math.max(0.5, Number(e.target.value)))}
          disabled={!!dryRun}
          className="w-20 px-1.5 py-1 rounded bg-surface border border-border text-text disabled:opacity-60"
        />
        <span className="text-muted">(current: {bytesToGb(currentMemoryBytes)})</span>
      </div>

      {error && <div className="text-bad mb-2">{error}</div>}

      {!dryRun ? (
        <div className="flex gap-2">
          <button
            disabled={pending}
            onClick={preview}
            className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50"
          >
            {pending ? "Checking…" : "Preview"}
          </button>
          <button onClick={() => setExpanded(false)} className="text-muted hover:text-text">
            cancel
          </button>
        </div>
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
            {dryRun.dry_run_result?.current_cores} → {dryRun.dry_run_result?.new_cores} vCPU ·{" "}
            {bytesToGb(dryRun.dry_run_result?.current_memory_bytes)}GB → {bytesToGb(dryRun.dry_run_result?.new_memory_bytes)}GB
            {dryRun.dry_run_result?.will_power_cycle && <span className="text-warn"> — will power cycle now</span>}
          </div>
          <div className="flex gap-2">
            <button
              disabled={pending}
              onClick={confirmApply}
              className="px-2 py-1 rounded bg-ink text-on-ink border border-warn hover:bg-warn/10 disabled:opacity-50"
            >
              {pending ? "Applying…" : "Confirm & Apply"}
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
