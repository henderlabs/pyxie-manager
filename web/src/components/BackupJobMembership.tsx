"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { isInFlight } from "@/lib/operationStatus";
import { useMe } from "@/lib/useMe";

type BackupJobMember = { workload_id: string; vmid: number; name: string | null; included: boolean };

type BackupJob = {
  job_id: string;
  cluster_id: string;
  cluster_name: string;
  storage: string;
  schedule: string | null;
  enabled: boolean;
  all_guests: boolean;
  exclude_vmids: number[];
  vmid_list: number[];
  members: BackupJobMember[];
  unknown_vmids: number[];
};

type DryRunOperation = {
  id: string;
  status: string;
  dry_run_result?: {
    desired_all: boolean;
    added: { vmid: number; name: string | null }[];
    removed: { vmid: number; name: string | null }[];
    reasons?: string[];
  };
};

export default function BackupJobMembership({ jobs }: { jobs: BackupJob[] }) {
  return (
    <div className="space-y-4">
      {jobs.length === 0 && <div className="text-sm text-muted">No PBS-backed backup jobs found.</div>}
      {jobs.map((job) => (
        <JobCard key={job.job_id} job={job} />
      ))}
    </div>
  );
}

function JobCard({ job }: { job: BackupJob }) {
  const router = useRouter();
  const [pendingAdd, setPendingAdd] = useState<Set<number>>(new Set());
  const [pendingRemove, setPendingRemove] = useState<Set<number>>(new Set());
  const [dryRun, setDryRun] = useState<DryRunOperation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  const effectiveIncluded = useMemo(() => {
    const included = new Set(job.members.filter((m) => m.included).map((m) => m.vmid));
    for (const v of pendingAdd) included.add(v);
    for (const v of pendingRemove) included.delete(v);
    return included;
  }, [job.members, pendingAdd, pendingRemove]);

  const hasPendingChanges = pendingAdd.size > 0 || pendingRemove.size > 0;

  function toggle(vmid: number, currentlyIncluded: boolean) {
    if (dryRun) return; // must confirm or cancel the pending preview first
    setPendingAdd((s) => {
      const next = new Set(s);
      if (!currentlyIncluded) next.add(vmid);
      else next.delete(vmid);
      return next;
    });
    setPendingRemove((s) => {
      const next = new Set(s);
      if (currentlyIncluded) next.add(vmid);
      else next.delete(vmid);
      return next;
    });
  }

  async function runDryRun(payload: { select_all?: boolean; add_vmids?: number[]; remove_vmids?: number[] }) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/protection/backup-jobs/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cluster_id: job.cluster_id, job_id: job.job_id, ...payload }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Preview failed");
        return;
      }
      setDryRun(data);
      notifyOperationsChanged();
    } finally {
      setBusy(false);
    }
  }

  function previewChanges() {
    return runDryRun({ add_vmids: Array.from(pendingAdd), remove_vmids: Array.from(pendingRemove) });
  }

  function previewSelectAll() {
    return runDryRun({ select_all: true });
  }

  async function cancelPreview() {
    if (dryRun) {
      fetch(`/api/operations/${dryRun.id}/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dismissed: true }),
      }).catch(() => {});
    }
    setDryRun(null);
    setPendingAdd(new Set());
    setPendingRemove(new Set());
    notifyOperationsChanged();
  }

  async function confirm() {
    if (!dryRun) return;
    const opId = dryRun.id;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/operations/${opId}/approve`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.error || `Approval failed (${res.status})`);
        return;
      }
      notifyOperationsChanged();
      let op: { status: string; error?: string | null; blocking_safety_rules?: string[] } = await res.json();
      while (isInFlight(op.status)) {
        await new Promise((r) => setTimeout(r, 1500));
        const opRes = await fetch(`/api/operations/${opId}`);
        if (!opRes.ok) break;
        op = await opRes.json();
      }
      notifyOperationsChanged();
      if (op.status === "failed" || op.status === "blocked" || op.status === "cancelled") {
        // Don't silently clear the pending diff as if this succeeded --
        // an operator clicking "Confirm & Apply" on a backup job needs to
        // know it did NOT go through, same as they would for a VM
        // operation. dryRun is cleared so the stale Confirm/cancel panel (still
        // showing the pre-attempt "awaiting_approval" status) disappears,
        // but pendingAdd/pendingRemove are left alone so the checklist
        // still reflects what was attempted and "Preview Changes" can be
        // retried without re-clicking every checkbox.
        setDryRun(null);
        setError(
          op.error || (op.blocking_safety_rules?.length ? op.blocking_safety_rules.join(", ") : `Operation ${op.status}`)
        );
        return;
      }
      setDryRun(null);
      setPendingAdd(new Set());
      setPendingRemove(new Set());
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border border-border rounded p-3 bg-surface2/40">
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className="text-sm text-text font-medium">{job.job_id}</div>
          <div className="text-xs text-muted">
            {job.cluster_name} · storage <span className="text-text">{job.storage}</span>
            {job.schedule && <> · {job.schedule}</>}
            {!job.enabled && <span className="text-warn"> · disabled</span>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {job.all_guests ? (
            <span className="text-xs px-2 py-1 rounded bg-good/10 text-good border border-good/30">
              All guests{job.exclude_vmids.length > 0 ? ` (except ${job.exclude_vmids.length})` : ""}
            </span>
          ) : (
            isAdmin && (
              <button
                disabled={busy || !!dryRun}
                onClick={previewSelectAll}
                className="text-xs px-2 py-1 rounded bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
                title="Switch this job to PVE's native all-guests mode -- every current AND future VM is backed up automatically from then on."
              >
                Select All
              </button>
            )
          )}
        </div>
      </div>

      {job.unknown_vmids.length > 0 && (
        <div className="text-xs text-warn mb-2">
          Job also lists VMID(s) {job.unknown_vmids.join(", ")} not known to PyXie's inventory (not shown below).
        </div>
      )}

      <div className="max-h-64 overflow-y-auto border border-border rounded divide-y divide-border">
        {job.members.map((m) => {
          const included = effectiveIncluded.has(m.vmid);
          const changed = pendingAdd.has(m.vmid) || pendingRemove.has(m.vmid);
          return (
            <label key={m.vmid} className="flex items-center gap-2 px-2 py-1.5 text-sm hover:bg-surface2/60 cursor-pointer">
              <input
                type="checkbox"
                checked={included}
                disabled={!!dryRun || !isAdmin}
                onChange={() => toggle(m.vmid, included)}
              />
              <span className={changed ? "text-warn" : "text-text"}>
                {m.name || `VMID ${m.vmid}`} <span className="text-muted">({m.vmid})</span>
              </span>
            </label>
          );
        })}
      </div>

      {error && <div className="text-xs text-bad mt-2">{error}</div>}

      {!dryRun && hasPendingChanges && (
        <div className="flex gap-2 mt-2">
          <button
            disabled={busy}
            onClick={previewChanges}
            className="text-xs px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50"
          >
            {busy ? "Checking…" : "Preview Changes"}
          </button>
          <button
            onClick={() => {
              setPendingAdd(new Set());
              setPendingRemove(new Set());
            }}
            className="text-xs text-muted hover:text-text"
          >
            reset
          </button>
        </div>
      )}

      {dryRun && dryRun.status === "blocked" && (
        <div className="mt-2 text-xs">
          <div className="text-bad mb-2">Blocked: {(dryRun.dry_run_result?.reasons || []).join("; ") || "see reasons"}</div>
          <button onClick={cancelPreview} className="text-muted hover:text-text">
            back
          </button>
        </div>
      )}

      {dryRun && dryRun.status !== "blocked" && (
        <div className="mt-2 text-xs">
          <div className="text-text mb-2">
            {dryRun.dry_run_result?.desired_all && <div>Switching to all-guests mode.</div>}
            {(dryRun.dry_run_result?.added?.length ?? 0) > 0 && (
              <div className="text-good">+ adding: {dryRun.dry_run_result!.added.map((a) => a.name || a.vmid).join(", ")}</div>
            )}
            {(dryRun.dry_run_result?.removed?.length ?? 0) > 0 && (
              <div className="text-bad">− removing: {dryRun.dry_run_result!.removed.map((r) => r.name || r.vmid).join(", ")}</div>
            )}
          </div>
          <div className="flex gap-2">
            <button
              disabled={busy}
              onClick={confirm}
              className="px-2 py-1 rounded bg-ink text-on-ink border border-warn hover:bg-warn/10 disabled:opacity-50"
            >
              {busy ? "Applying…" : "Confirm & Apply"}
            </button>
            <button onClick={cancelPreview} className="text-muted hover:text-text">
              cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
