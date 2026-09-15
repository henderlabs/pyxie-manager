"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { isInFlight } from "@/lib/operationStatus";
import { PlayIcon, StopIcon, RestartIcon } from "@/components/Icons";
import { useMe } from "@/lib/useMe";

type Action = "start" | "shutdown" | "reboot";

type DryRunOperation = {
  id: string;
  status: string;
  dry_run_result?: {
    reasons?: string[];
  };
};

const ACTION_META: Record<Action, { label: string; icon: typeof PlayIcon; requiredStatus: string }> = {
  start: { label: "Start", icon: PlayIcon, requiredStatus: "stopped" },
  shutdown: { label: "Shut down", icon: StopIcon, requiredStatus: "running" },
  reboot: { label: "Restart", icon: RestartIcon, requiredStatus: "running" },
};

// Play/Stop/Restart icon buttons for the Workloads page. Each hits the
// same /workload-lifecycle/dry-run -> approve path
// WorkloadLifecycleForm's dropdown already uses on the Maintenance page --
// this is just a faster, icon-based way to reach the same three actions
// (a fourth, force_stop, deliberately stays dropdown-only there: it's the
// higher-risk one that wants a justification typed in, not a one-click
// icon). Only the action matching the workload's CURRENT status is ever
// clickable -- Start when stopped, Shut down/Restart when running -- so
// there's no need to preview an action PVE would just reject outright.
export default function WorkloadLifecycleButtons({
  workloadId,
  workloadName,
  status,
  disabled,
  disabledReason,
}: {
  workloadId: string;
  workloadName: string;
  status: string;
  /** Guest lifecycle actions are VM-only on the backend today -- LXC isn't
   * implemented. Pass true for containers so the buttons show as
   * unavailable instead of failing after a click. */
  disabled?: boolean;
  disabledReason?: string;
}) {
  const router = useRouter();
  const [active, setActive] = useState<Action | null>(null);
  const [dryRun, setDryRun] = useState<DryRunOperation | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  async function preview(action: Action) {
    setActive(action);
    setDryRun(null);
    setError(null);
    setPending(true);
    notifyOperationsChanged();
    try {
      const res = await fetch("/api/operations/workload-lifecycle/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workload_id: workloadId, action }),
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

  async function cancel() {
    if (dryRun) {
      fetch(`/api/operations/${dryRun.id}/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dismissed: true }),
      }).catch(() => {});
    }
    setActive(null);
    setDryRun(null);
    setError(null);
    notifyOperationsChanged();
    router.refresh();
  }

  async function confirm() {
    if (!dryRun) return;
    const opId = dryRun.id;
    setPending(true);
    try {
      const res = await fetch(`/api/operations/${opId}/approve`, { method: "POST" });
      if (!res.ok) return;
      notifyOperationsChanged();
      setActive(null);
      setDryRun(null);
      // Same reasoning as ApplyRightsizingForm/ApplyPlacementForm: poll to
      // an actual terminal status (not a hand-picked in-flight list) in
      // the background before refreshing, so the table's Status column
      // reflects the real outcome instead of a still-mid-flight snapshot.
      let s: string = (await res.json()).status;
      while (isInFlight(s)) {
        await new Promise((r) => setTimeout(r, 2000));
        const opRes = await fetch(`/api/operations/${opId}`);
        if (!opRes.ok) break;
        s = (await opRes.json()).status;
      }
      notifyOperationsChanged();
      router.refresh();
    } finally {
      setPending(false);
    }
  }

  if (active) {
    const meta = ACTION_META[active];
    return (
      <div className="flex items-center gap-2 text-xs whitespace-nowrap">
        {error ? (
          <>
            <span className="text-bad">{error}</span>
            <button onClick={cancel} className="text-muted hover:text-text">
              back
            </button>
          </>
        ) : !dryRun ? (
          <span className="text-muted">Checking…</span>
        ) : dryRun.status === "blocked" ? (
          <>
            <span className="text-bad" title={(dryRun.dry_run_result?.reasons || []).join("; ")}>
              Blocked ⓘ
            </span>
            <button onClick={cancel} className="text-muted hover:text-text">
              back
            </button>
          </>
        ) : (
          <>
            <span className="text-warn">
              {meta.label} {workloadName}?
            </span>
            <button
              disabled={pending}
              onClick={confirm}
              className="px-2 py-0.5 rounded bg-black text-white border border-warn hover:bg-warn/10 disabled:opacity-50"
            >
              {pending ? "…" : "Confirm"}
            </button>
            <button onClick={cancel} className="text-muted hover:text-text">
              cancel
            </button>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      {(Object.keys(ACTION_META) as Action[]).map((action) => {
        const meta = ACTION_META[action];
        const Icon = meta.icon;
        const enabled = !disabled && status === meta.requiredStatus;
        return (
          <button
            key={action}
            disabled={!enabled}
            onClick={() => preview(action)}
            title={
              disabled
                ? disabledReason
                : enabled
                ? meta.label
                : `${meta.label} -- requires status '${meta.requiredStatus}' (currently '${status}')`
            }
            className="p-1 rounded border border-border text-muted hover:text-text hover:border-accent disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:text-muted disabled:hover:border-border"
          >
            <Icon className="w-3.5 h-3.5" />
          </button>
        );
      })}
    </div>
  );
}
