"use client";

import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle, EmptyState } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import { HostUpdatePlan } from "@/components/OperationCard";
import { MigrateIcon, WorkloadIcon, PackageIcon, ClockIcon, WrenchIcon, CpuIcon, PlugIcon, RestartIcon } from "@/components/Icons";
import { notifyOperationsChanged, onOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

const TERMINAL_STATUSES = ["completed", "failed", "blocked", "cancelled"];
const TYPE_LABELS: Record<string, string> = {
  "vm.live_migrate": "Live Migration",
  "node.evacuate": "Node Evacuation",
  "workload.shutdown": "Guest Shutdown",
  "workload.start": "Guest Start",
  "workload.force_stop": "Guest Force Stop",
  "workload.reboot": "Guest Reboot",
  "workload.resize": "Guest Resize",
  "host.update": "Host Update Preflight",
  "host.reboot": "Host Reboot",
  "maintenance.run": "Full Maintenance Run",
  "node.enter_maintenance": "Enter Maintenance Mode",
  "node.exit_maintenance": "Exit Maintenance Mode",
  "cluster.rebalance": "Balance Load",
};
const TYPE_ICONS: Record<string, React.ReactNode> = {
  "vm.live_migrate": <MigrateIcon className="w-4 h-4" />,
  "node.evacuate": <MigrateIcon className="w-4 h-4" />,
  "workload.shutdown": <WorkloadIcon className="w-4 h-4" />,
  "workload.start": <WorkloadIcon className="w-4 h-4" />,
  "workload.force_stop": <WorkloadIcon className="w-4 h-4" />,
  "workload.reboot": <RestartIcon className="w-4 h-4" />,
  "workload.resize": <CpuIcon className="w-4 h-4" />,
  "host.update": <PackageIcon className="w-4 h-4" />,
  "host.reboot": <ClockIcon className="w-4 h-4" />,
  "maintenance.run": <WrenchIcon className="w-4 h-4" />,
  "node.enter_maintenance": <WrenchIcon className="w-4 h-4" />,
  "node.exit_maintenance": <PlugIcon className="w-4 h-4" />,
  "cluster.rebalance": <MigrateIcon className="w-4 h-4" />,
};

const RELEVANT_TYPES = new Set(Object.keys(TYPE_LABELS));

function subtitleFor(op: Operation): string {
  if (op.operation_type_id === "vm.live_migrate") {
    const r = op.dry_run_result;
    const name = r?.workload_name || (r?.vmid ? `vmid ${r.vmid}` : "?");
    return `${name} — ${r?.source_node || "?"} → ${r?.target_node || "?"}`;
  }
  if (op.operation_type_id === "cluster.rebalance") {
    // Not tied to one node -- dry_run_result.node is always null here.
    const count = (op.dry_run_result?.migrate_plan as unknown[] | undefined)?.length ?? 0;
    return count > 0 ? `${count} workload${count === 1 ? "" : "s"}` : "already balanced";
  }
  const node = String(op.dry_run_result?.node || "?");
  if (op.operation_type_id === "host.update") {
    // Reboot-required is only a real, post-apply fact once the patch has
    // actually run (verification_result) -- before that, dry_run_result's
    // hint is a preflight guess. A completed patch run used to give no
    // indication either way of whether it needed a reboot.
    const vr = op.verification_result as Record<string, unknown> | null;
    const dr = op.dry_run_result as Record<string, unknown> | null;
    const count = (vr?.applied_packages as unknown[] | undefined)?.length ?? (dr?.planned_package_count as number | undefined);
    const rebootRequired = vr ? Boolean(vr.reboot_required) : dr ? Boolean(dr.reboot_required_hint) : null;
    const countText = count != null ? ` · ${count} package${count === 1 ? "" : "s"}` : "";
    const rebootText = rebootRequired == null ? "" : ` · reboot ${rebootRequired ? "required" : "not required"}`;
    return `${node}${countText}${rebootText}`;
  }
  if (op.operation_type_id === "maintenance.run") {
    const ctx = op.context as Record<string, unknown> | null;
    const rebootRequired = ctx?.reboot_required;
    const forced = Boolean(ctx?.force_reboot) && rebootRequired === false;
    const rebootText =
      rebootRequired == null ? "" : ` · reboot ${rebootRequired ? "required" : forced ? "not required (forced anyway)" : "not required"}`;
    return `${node}${rebootText}`;
  }
  return node;
}

const DAY_MS = 24 * 60 * 60 * 1000;

export default function NodeOperationsLog({ initialOperations }: { initialOperations: Operation[] }) {
  const [all, setAll] = useState<Operation[]>(initialOperations);
  const [showDismissed, setShowDismissed] = useState(false);
  const [approving, setApproving] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  // Older items fall off THIS view only -- they stay fully intact in the
  // Audit Log, nothing is deleted here, this is display-only. Applied on
  // top of the dismissed filter, not instead of it.
  const recent = all.filter((o) => Date.now() - new Date(o.created_at).getTime() < DAY_MS);
  const visible = showDismissed ? recent : recent.filter((o) => !o.dismissed);
  const dismissedCount = recent.filter((o) => o.dismissed).length;

  // Same pattern as TaskPanel.tsx: a 5s poll so a completion/new-operation
  // shows up on its own, PLUS an immediate refresh on the operations bus so
  // it doesn't wait up to 5s for something that just happened elsewhere on
  // the page. Fully replaces the list each time (not a per-row merge) --
  // that's what makes a brand-new operation (e.g. a reboot just triggered
  // from Node Actions) appear here without a manual page reload, which a
  // per-row-only poll of already-known ids could never do -- a real gap
  // found live, a just-triggered reboot didn't show up until a couple of
  // page refreshes.
  function refresh() {
    fetch("/api/operations?limit=200")
      .then((r) => (r.ok ? r.json() : []))
      .then((data: Operation[]) => {
        if (Array.isArray(data)) setAll(data.filter((o) => RELEVANT_TYPES.has(o.operation_type_id)));
      })
      .catch(() => {});
  }

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    const unsubscribe = onOperationsChanged(refresh);
    return () => {
      clearInterval(interval);
      unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function toggleDismiss(op: Operation) {
    const res = await fetch(`/api/operations/${op.id}/dismiss`, {
      method: "POST",
      body: JSON.stringify({ dismissed: !op.dismissed }),
    });
    if (res.ok) {
      refresh();
      notifyOperationsChanged();
    }
  }

  async function approve(op: Operation) {
    setApproving(op.id);
    try {
      const res = await fetch(`/api/operations/${op.id}/approve`, { method: "POST" });
      if (res.ok) {
        refresh();
        notifyOperationsChanged();
      }
    } finally {
      setApproving(null);
    }
  }

  return (
    <Card>
      <div className="flex items-center justify-between mb-1">
        <CardTitle>Node &amp; Guest Operations</CardTitle>
        {dismissedCount > 0 && (
          <button onClick={() => setShowDismissed((s) => !s)} className="text-xs text-accent hover:underline">
            {showDismissed ? "Hide dismissed" : `Show ${dismissedCount} dismissed`}
          </button>
        )}
      </div>
      <p className="text-xs text-muted mb-2">Last 24 hours -- older activity is still in the Audit Log.</p>
      {visible.length === 0 ? (
        <EmptyState message="Nothing in the last 24 hours." />
      ) : (
        <div className="divide-y divide-border">
          {visible.map((op) => (
            <div key={op.id} className={`py-2 ${op.dismissed ? "opacity-50" : ""}`}>
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm text-text flex items-center gap-1.5">
                    <span className="text-muted">{TYPE_ICONS[op.operation_type_id]}</span>
                    {TYPE_LABELS[op.operation_type_id] || op.operation_type_id} — {subtitleFor(op)}
                  </div>
                  <div className="text-xs text-muted">{new Date(op.created_at).toLocaleString()} · {op.created_by}</div>
                  {op.status === "blocked" && op.blocking_safety_rules && op.blocking_safety_rules.length > 0 && (
                    <div className="text-xs text-bad mt-0.5">Blocked by: {op.blocking_safety_rules.join(", ")}</div>
                  )}
                  {op.status === "failed" && op.error && <div className="text-xs text-bad mt-0.5">{op.error}</div>}
                </div>
                <div className="flex items-center gap-2">
                  {op.stage && <span className="text-xs text-muted">{op.stage}</span>}
                  <StatusBadge status={op.status} />
                  {op.status === "awaiting_approval" && !op.dismissed && isAdmin && (
                    <button
                      onClick={() => approve(op)}
                      disabled={approving === op.id}
                      className="px-2 py-1 rounded text-xs font-medium bg-black text-white border border-warn hover:bg-warn/10 disabled:opacity-50"
                    >
                      {approving === op.id ? "Submitting…" : "Approve & Execute"}
                    </button>
                  )}
                  {(TERMINAL_STATUSES.includes(op.status) || op.status === "awaiting_approval") && isAdmin && (
                    <button onClick={() => toggleDismiss(op)} className="text-xs text-muted hover:underline">
                      {op.dismissed ? "Restore" : "Dismiss"}
                    </button>
                  )}
                </div>
              </div>
              {op.operation_type_id === "host.update" && op.dry_run_result?.planned_packages != null && (
                <div className="mt-1.5">
                  <HostUpdatePlan result={op.dry_run_result} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
