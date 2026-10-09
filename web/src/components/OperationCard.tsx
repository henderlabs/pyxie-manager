"use client";

import MigrationBars from "@/components/MigrationBars";
import { useState } from "react";
import OperationLogPanel from "@/components/OperationLogPanel";
import type { Operation } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import { isInFlight } from "@/lib/operationStatus";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

// Mirrors _COOPERATIVE_CANCEL_TYPES in operations.py -- these are the only
// types whose execute loop actually checks for a cancel request between
// plan items. Everything else (a single vm.live_migrate, host.update,
// host.reboot) has no safe point to stop at once PVE has started moving
// bytes or installing packages, so Cancel is only offered for those while
// they're still awaiting_approval (see CancelButton below).
const COOPERATIVE_CANCEL_TYPES = new Set(["node.enter_maintenance", "maintenance.run", "node.evacuate", "cluster.rebalance"]);

function CancelButton({ op, onCancelled }: { op: Operation; onCancelled: (updated: Operation) => void }) {
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  if (me !== undefined && me?.is_admin !== true) return null;

  async function cancel() {
    setCancelling(true);
    setError(null);
    try {
      const res = await fetch(`/api/operations/${op.id}/cancel`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || data.detail || "Could not cancel");
        return;
      }
      onCancelled(data as Operation);
      // Without this, cancelling here only self-corrects in the Tasks
      // panel on its next 5s poll -- everywhere else that changes an
      // operation's status fires this immediately instead of leaving the
      // stale awaiting_approval entry sitting there for up to 5s --
      // cancelling in the plan editor should also clear it from Awaiting
      // Approval in the task bar right away.
      notifyOperationsChanged();
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="inline-flex flex-col">
      <button
        onClick={cancel}
        disabled={cancelling}
        className="px-3 py-1.5 rounded text-sm font-medium text-muted hover:text-bad hover:bg-bad/10 border border-border disabled:opacity-50"
        title={
          isInFlight(op.status)
            ? "Stop before the next planned step -- whatever's already running or already done is not undone."
            : "Cancel this plan without applying it."
        }
      >
        {cancelling ? "Cancelling…" : "Cancel"}
      </button>
      {error && <span className="text-xs text-bad mt-1">{error}</span>}
    </div>
  );
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "?";
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(0)} MiB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

const STAGE_LABELS: Record<string, string> = {
  dry_run: "Checking eligibility",
  preflight: "Checking eligibility",
  awaiting_approval: "Waiting for approval",
  revalidating: "Revalidating against live PVE state",
  shutting_down: "Shutting down guest",
  starting_up: "Powering guest back on",
  executing: "Submitting to PVE",
  evacuate: "Evacuating workloads",
  monitoring: "In progress",
  verify_evacuation: "Verifying evacuation",
  patch: "Patching host packages",
  reboot: "Rebooting host",
  verify_node: "Verifying node",
  restore_placement: "Restoring original placement",
  verify_workloads: "Verifying workloads",
  audit: "Done",
};

export const OPERATION_TYPE_LABELS: Record<string, string> = {
  "vm.live_migrate": "Live Migration",
  "node.evacuate": "Node Evacuation",
  "workload.shutdown": "Guest Shutdown",
  "workload.start": "Guest Start",
  "workload.force_stop": "Guest Force Stop",
  "workload.reboot": "Guest Reboot",
  "host.update": "Host Package Update",
  "host.reboot": "Host Reboot",
  "maintenance.run": "Full Maintenance Run",
  "node.enter_maintenance": "Enter Maintenance Mode",
  "node.exit_maintenance": "Exit Maintenance Mode",
  "cluster.rebalance": "Balance Load",
  "workload.network_vlan_change": "Network VLAN Change",
};

/** Bulk Migrate is a cluster.rebalance whose plan was built from ticked VMs
 *  (context.mode === "bulk_migrate"); everywhere that shows a title uses this
 *  so it reads "Bulk Migrate", not "Balance Load". */
export function operationTypeLabel(op: { operation_type_id: string; context?: Record<string, unknown> | null }): string {
  if (op.operation_type_id === "cluster.rebalance" && op.context?.mode === "bulk_migrate") return "Bulk Migrate";
  return OPERATION_TYPE_LABELS[op.operation_type_id] || op.operation_type_id;
}

function operationSummary(op: Operation): string {
  const r = op.dry_run_result;
  if (op.operation_type_id === "vm.live_migrate") {
    const storagePart = r?.target_storage
      ? ` · storage → ${r.target_storage}`
      : r?.current_storage
      ? ` · storage: ${r.current_storage} (unchanged)`
      : "";
    return `${r?.workload_name || `vmid ${r?.vmid}`}${r?.workload_name ? ` (vmid ${r?.vmid})` : ""} — ${r?.source_node} → ${r?.target_node}${storagePart}`;
  }
  if (op.operation_type_id === "host.update") {
    const count = r?.planned_package_count;
    return `${OPERATION_TYPE_LABELS[op.operation_type_id]} — ${r?.node || "?"}${count != null ? ` · ${count} package${count === 1 ? "" : "s"}` : ""}`;
  }
  if (
    op.operation_type_id === "node.evacuate" || op.operation_type_id === "host.reboot" ||
    op.operation_type_id === "maintenance.run" || op.operation_type_id === "node.enter_maintenance" ||
    op.operation_type_id === "node.exit_maintenance"
  ) {
    return `${OPERATION_TYPE_LABELS[op.operation_type_id]} — ${r?.node || "?"}`;
  }
  if (op.operation_type_id === "cluster.rebalance") {
    const count = (r?.migrate_plan as unknown[] | undefined)?.length ?? 0;
    return `${operationTypeLabel(op)} — ${count} workload${count === 1 ? "" : "s"} planned`;
  }
  if (["workload.shutdown", "workload.start", "workload.force_stop", "workload.reboot"].includes(op.operation_type_id)) {
    return `${OPERATION_TYPE_LABELS[op.operation_type_id]} — ${r?.workload_name || `vmid ${r?.vmid}`}${r?.workload_name ? ` (vmid ${r?.vmid})` : ""} on ${r?.node || "?"}`;
  }
  return `${OPERATION_TYPE_LABELS[op.operation_type_id] || op.operation_type_id}`;
}

// node.enter_maintenance/maintenance.run key their plan "migrate_plan";
// node.evacuate keys the identical shape "plan" (see operations.py's
// _PLAN_KEY_BY_TYPE). Either way, each item carries the FULL ranked
// candidate list from planning, not just the auto-pick.
const PLAN_KEY_BY_TYPE: Record<string, string> = {
  "node.enter_maintenance": "migrate_plan",
  "maintenance.run": "migrate_plan",
  "node.evacuate": "plan",
  "cluster.rebalance": "migrate_plan",
};

// TRULY non-negotiable buckets only -- a structural/compatibility reason
// (PCI passthrough, no destination at all, downtime_tolerance='low' with
// nowhere to go) that a human can't wave past by picking a destination,
// so these stay read-only. Everything else that isn't in the main editable
// plan gets the interactive opt-in treatment below instead -- a real gap
// found live: the plan preview was missing a workload entirely because it
// was in shutdown_plan with nothing on screen saying so. Every workload
// should be displayed for choice of handling regardless of headroom --
// headroom specifically is a resource judgment call, not a structural
// fact, so it no longer belongs in this read-only bucket.
const OTHER_BUCKETS_BY_TYPE: Record<string, { key: string; label: string }[]> = {
  "node.enter_maintenance": [
    { key: "blocked_workloads", label: "Blocked" },
    { key: "stopped_no_action", label: "Stopped -- no action needed" },
  ],
  "maintenance.run": [
    { key: "blocked_workloads", label: "Blocked" },
    { key: "stopped_no_action", label: "Stopped -- no action needed" },
  ],
  "node.evacuate": [
    { key: "unmigratable", label: "No destination found" },
    { key: "stopped_no_action", label: "Stopped -- no action needed" },
  ],
  "cluster.rebalance": [],
};

// Every context key that holds a workload the automated plan didn't put in
// the main editable list, but which still gets a real (if possibly
// flagged) destination + an "included" opt-in toggle: shutdown_plan (a
// running workload with no clean auto-pick -- commonly insufficient
// headroom, which is a resource judgment call, not a hard block -- so it
// defaults to shutting down in place but can be opted into a real move
// instead) and stopped_relocation_required (a stopped workload that never
// NEEDS to move, offered anyway in case the node is leaving the cluster
// for good). One type can have both at once (maintenance.run with "leave
// empty"); the update endpoint figures out which list actually holds a
// given workload_id, so the frontend doesn't need to track that itself.
// A stopped workload should still have the option to move if
// maintenance includes removing the host from the cluster, and every
// workload should be displayed for choice of handling regardless of
// headroom.
const OPTIONAL_PLAN_KEYS_BY_TYPE: Record<string, string[]> = {
  "node.enter_maintenance": ["shutdown_plan", "stopped_relocation_required"],
  "maintenance.run": ["shutdown_plan", "stopped_relocation_required"],
  "node.evacuate": ["stopped_relocation_required"],
};

type PlanCandidate = {
  node_id: string; node_name: string; score: number;
  blocked: boolean; blocking_reasons: string[]; reasons: string[];
};
type MigratePlanItem = {
  workload_id: string; vmid: number; name: string | null;
  destination_node_id: string; destination_node: string;
  candidates?: PlanCandidate[];
  currently_on_shared?: boolean;
  current_storage?: string | null;
  destination_storage?: string | null;
  destination_storage_scope?: string | null;
  source_node?: string;
  memory_bytes?: number | null;
  pinned_node?: string | null;
  // "shutdown_in_place" opts a workload that DOES have a clean auto-found
  // destination out of migrating at all -- just power it off for the
  // maintenance window and back on here afterward (node.exit_maintenance's
  // own restart_plan already restarts anything left in completed_shutdowns,
  // so this needs no separate restore plumbing). Only offered for
  // node.enter_maintenance/maintenance.run, where the node stays in the
  // cluster -- meaningless for node.evacuate/cluster.rebalance, which are
  // about moving load OFF a node for good. Some workloads can stay in
  // place and just be powered down during updates, then brought back up
  // once updates/reboot are complete, without migrating at all.
  transport?: "live" | "offline" | "shutdown_in_place" | "skip";
};
type OtherPlanItem = {
  workload_id: string; vmid: number; name: string | null;
  reasons?: string[];
  // A stopped VM with Start at boot on: the host reboot will power it on by itself.
  starts_on_boot?: boolean;
};
type OptionalPlanItem = {
  workload_id: string; vmid: number; name: string | null;
  reasons?: string[];
  included?: boolean;
  destination_node_id?: string; destination_node?: string;
  candidates?: PlanCandidate[];
  // shutdown_plan rows are a currently-running guest (live vs. offline
  // transport is a real choice); stopped_relocation_required rows are
  // already off, and the UI should note when a workload is stopped vs.
  // running.
  currently_running?: boolean;
  transport?: "live" | "offline";
};

function MigratePlanEditor({ op, planKey, onUpdated }: { op: Operation; planKey: string; onUpdated: (updated: Operation) => void }) {
  const dryRun = (op.dry_run_result as Record<string, unknown> | null) || {};
  const plan = (dryRun[planKey] as MigratePlanItem[] | undefined) || [];
  const otherBuckets = OTHER_BUCKETS_BY_TYPE[op.operation_type_id] || [];
  const otherGroups = otherBuckets
    .map((b) => ({ label: b.label, items: (dryRun[b.key] as OtherPlanItem[] | undefined) || [] }))
    .filter((g) => g.items.length > 0);
  const otherCount = otherGroups.reduce((n, g) => n + g.items.length, 0);
  const optionalKeys = OPTIONAL_PLAN_KEYS_BY_TYPE[op.operation_type_id] || [];
  const optionalItems = optionalKeys.flatMap((k) => (dryRun[k] as OptionalPlanItem[] | undefined) || []);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [rescoring, setRescoring] = useState(false);
  const [rescoreError, setRescoreError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  if (plan.length === 0 && otherCount === 0 && optionalItems.length === 0) return null;

  async function changeDestination(item: MigratePlanItem, nodeId: string) {
    if (nodeId === item.destination_node_id) return;
    setSavingId(item.workload_id);
    setSaveError(null);
    try {
      const res = await fetch(`/api/operations/${op.id}/migrate-plan/${item.workload_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ destination_node_id: nodeId }),
      });
      const data = await res.json();
      if (!res.ok) {
        setSaveError(data.error || data.detail || "Could not change destination");
        return;
      }
      onUpdated(data as Operation);
    } finally {
      setSavingId(null);
    }
  }

  async function changeTransport(item: MigratePlanItem, transport: "live" | "offline" | "shutdown_in_place" | "skip") {
    if (transport === (item.transport || "live")) return;
    setSavingId(item.workload_id);
    setSaveError(null);
    try {
      const res = await fetch(`/api/operations/${op.id}/migrate-plan/${item.workload_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transport }),
      });
      const data = await res.json();
      if (!res.ok) {
        setSaveError(data.error || data.detail || "Could not change transport");
        return;
      }
      onUpdated(data as Operation);
    } finally {
      setSavingId(null);
    }
  }

  async function changeOptionalIncluded(item: OptionalPlanItem, included: boolean) {
    setSavingId(item.workload_id);
    setSaveError(null);
    try {
      const res = await fetch(`/api/operations/${op.id}/stopped-relocation/${item.workload_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ included }),
      });
      const data = await res.json();
      if (!res.ok) {
        setSaveError(data.error || data.detail || "Could not change this");
        return;
      }
      onUpdated(data as Operation);
    } finally {
      setSavingId(null);
    }
  }

  async function changeOptionalDestination(item: OptionalPlanItem, nodeId: string) {
    if (nodeId === item.destination_node_id) return;
    setSavingId(item.workload_id);
    setSaveError(null);
    try {
      const res = await fetch(`/api/operations/${op.id}/stopped-relocation/${item.workload_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ destination_node_id: nodeId }),
      });
      const data = await res.json();
      if (!res.ok) {
        setSaveError(data.error || data.detail || "Could not change destination");
        return;
      }
      onUpdated(data as Operation);
    } finally {
      setSavingId(null);
    }
  }

  async function changeOptionalTransport(item: OptionalPlanItem, transport: "live" | "offline") {
    if (transport === (item.transport || "live")) return;
    setSavingId(item.workload_id);
    setSaveError(null);
    try {
      const res = await fetch(`/api/operations/${op.id}/stopped-relocation/${item.workload_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transport }),
      });
      const data = await res.json();
      if (!res.ok) {
        setSaveError(data.error || data.detail || "Could not change transport");
        return;
      }
      onUpdated(data as Operation);
    } finally {
      setSavingId(null);
    }
  }

  async function rescore() {
    setRescoring(true);
    setRescoreError(null);
    try {
      const res = await fetch(`/api/operations/${op.id}/rescore`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setRescoreError(data.error || data.detail || "Could not re-score");
        return;
      }
      onUpdated(data as Operation);
    } finally {
      setRescoring(false);
    }
  }

  const totalCount = plan.length + otherCount + optionalItems.length;
  const includedOptionalCount = optionalItems.filter((i) => i.included).length;

  return (
    <div className="mb-3 border border-border rounded">
      <div className="flex items-center justify-between gap-2 px-2 py-1.5 border-b border-border">
        <div className="text-xs font-medium text-text">
          {totalCount} workload{totalCount === 1 ? "" : "s"}{op.operation_type_id === "cluster.rebalance" ? "" : " on this host"}
          {plan.length > 0 && (
            <>
              {" "}— {plan.filter((i) => i.transport !== "skip").length} to move (change the destination below if you don't agree with the pick)
              {plan.some((i) => i.transport === "skip") && <>, {plan.filter((i) => i.transport === "skip").length} set to Don't move</>}
            </>
          )}
          {otherCount > 0 && <> — {otherCount} {otherCount === 1 ? "isn't" : "aren't"} moving, see below</>}
          {optionalItems.length > 0 && (
            <> — {optionalItems.length} not required to move{includedOptionalCount > 0 ? ` (${includedOptionalCount} opted in)` : ""}</>
          )}
        </div>
        {plan.length > 0 && isAdmin && (
          <button
            onClick={rescore}
            disabled={rescoring || op.status !== "awaiting_approval"}
            className="shrink-0 px-2 py-1 rounded text-xs font-medium text-muted hover:text-text hover:bg-surface border border-border disabled:opacity-50"
            title="Recompute destinations against current live state. Manually-picked destinations are kept as-is."
          >
            {rescoring ? "Re-scoring…" : "Re-score"}
          </button>
        )}
      </div>
      {rescoreError && <div className="px-2 py-1.5 text-xs text-bad border-b border-border">{rescoreError}</div>}
      {/* Every workload on the host in one scrollable list, not just the
          ones with a destination -- a fixed max-height here (rather than
          letting the card grow unbounded) is what keeps this usable once a
          host carries dozens of guests instead of ~10: every workload on
          a host needs to be viewable, even if that means a scrollbar. */}
      <div className="divide-y divide-border max-h-96 overflow-y-auto">
        {plan.map((item) => (
          <div
            key={item.workload_id}
            className={`flex items-center justify-between px-2 py-1.5 text-xs gap-2 ${item.transport === "skip" ? "opacity-60" : ""}`}
          >
            <span className={`text-text truncate flex-1 min-w-0 ${item.transport === "skip" ? "line-through" : ""}`}>
              {item.name || `vmid ${item.vmid}`}
            </span>
            {op.operation_type_id === "cluster.rebalance" && item.source_node && (
              <span className="text-muted shrink-0 hidden sm:inline">
                from {item.source_node}{item.memory_bytes ? ` · ${formatBytes(item.memory_bytes)}` : ""}
              </span>
            )}
            {op.operation_type_id === "cluster.rebalance" && item.pinned_node && (
              <span className="shrink-0 px-1.5 py-0.5 rounded text-xs bg-accent/15 text-accent" title="Pinned host: a soft pin set on the workload page">
                {item.pinned_node === item.destination_node ? "moving to its pin" : `pinned to ${item.pinned_node}`}
              </span>
            )}
            {item.transport !== "skip" && item.transport !== "shutdown_in_place" && (item.current_storage || item.destination_storage) && (
              <span
                className="text-muted shrink-0 hidden sm:inline"
                title={
                  `Storage: now ${item.current_storage || "unknown"}${item.currently_on_shared ? " (shared -- reachable from any node)" : " (node-local)"}. ` +
                  (item.destination_storage
                    ? `After the move: ${item.destination_storage} (${item.destination_storage_scope === "cluster-shared" ? "shared" : "node-local on the destination"}); the disks are copied there. Informational: it follows the destination host's preferred storage.`
                    : "No storage change planned: the disks stay where they are.")
                }
              >
                {item.destination_storage ? (
                  <>
                    {item.current_storage || "?"} → <span className="text-text">{item.destination_storage}</span>
                  </>
                ) : (
                  <>on {item.current_storage} (stays)</>
                )}
              </span>
            )}
            <select
              value={item.transport || "live"}
              disabled={savingId === item.workload_id || op.status !== "awaiting_approval" || !isAdmin}
              onChange={(e) => changeTransport(item, e.target.value as "live" | "offline" | "shutdown_in_place" | "skip")}
              title={
                item.currently_on_shared === false
                  ? "This disk is on node-local storage -- live has to block-mirror it while the guest keeps writing; offline (shutdown, migrate, power back on) is usually faster, at the cost of downtime for the copy."
                  : "Live keeps the guest running throughout. Offline shuts it down first, copies, then powers it back on."
              }
              // Same fixed-width treatment as the destination select below --
              // an independent per-row choice, not tied to the destination.
              className="w-40 shrink-0 bg-surface2 border border-border rounded px-1.5 py-1 text-xs text-left disabled:opacity-50"
            >
              <option value="live">Live</option>
              <option value="offline">Shutdown + migrate</option>
              {/* Only makes sense when the node stays in the cluster --
                  node.evacuate/cluster.rebalance don't offer this. */}
              {(op.operation_type_id === "node.enter_maintenance" || op.operation_type_id === "maintenance.run") && (
                <option value="shutdown_in_place">Shut down, restart after</option>
              )}
              {/* Balance Load / Bulk Migrate only: every move there is optional, so
                  any suggestion can be dropped and the rest applied. */}
              {op.operation_type_id === "cluster.rebalance" && <option value="skip">Don't move</option>}
            </select>
            <select
              value={item.transport === "shutdown_in_place" || item.transport === "skip" ? "" : item.destination_node_id}
              disabled={savingId === item.workload_id || op.status !== "awaiting_approval" || item.transport === "shutdown_in_place" || item.transport === "skip" || !isAdmin}
              onChange={(e) => changeDestination(item, e.target.value)}
              title={item.transport === "shutdown_in_place" ? "Not moving -- stays on this host, powered off until maintenance completes." : undefined}
              className="w-52 shrink-0 bg-surface2 border border-border rounded px-1.5 py-1 text-xs text-left disabled:opacity-50"
            >
              {(item.transport === "shutdown_in_place" || item.transport === "skip") && <option value="">stays on this host</option>}
              {(item.candidates || []).map((c) => (
                <option key={c.node_id} value={c.node_id}>
                  {c.node_name}
                  {c.blocked ? ` — ⚠ ${c.blocking_reasons[0] || "flagged"}` : ` (score ${c.score})`}
                </option>
              ))}
            </select>
          </div>
        ))}
        {otherGroups.map((group) =>
          group.items.map((item) => (
            <div key={item.workload_id} className="flex items-center justify-between px-2 py-1.5 text-xs gap-2 bg-warn/5">
              <span className="text-text truncate flex-1 min-w-0">{item.name || `vmid ${item.vmid}`}</span>
              {item.starts_on_boot && (
                <span
                  className="shrink-0 text-[10px] uppercase tracking-wide text-warn font-semibold border border-warn/40 rounded px-1 py-0.5"
                  title={(item.reasons || [])[0]}
                >
                  starts on reboot
                </span>
              )}
              <span
                className="text-muted text-right max-w-[60%] truncate"
                title={(item.reasons || []).join("; ") || undefined}
              >
                <span className="text-warn font-medium">{group.label}</span>
                {item.reasons && item.reasons.length > 0 && <> — {item.reasons[0]}</>}
              </span>
            </div>
          ))
        )}
        {optionalItems.map((item) => (
          <div key={item.workload_id} className="flex items-center justify-between px-2 py-1.5 text-xs gap-2">
            <span className="text-text truncate flex-1 min-w-0" title={(item.reasons || []).join("; ") || undefined}>
              {item.name || `vmid ${item.vmid}`}
            </span>
            {/* Explicit power-state badge, not just implied by wording --
                the UI should note when a workload is stopped vs. running. */}
            <span
              className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium ${
                item.currently_running ? "bg-good/15 text-good" : "bg-muted/15 text-muted"
              }`}
            >
              {item.currently_running ? "Running" : "Stopped"}
            </span>
            {/* Spells out both outcomes in words instead of a bare
                checkbox next to an already-filled destination, which read
                as an already-made decision rather than a real choice --
                a real usability gap found live. */}
            <select
              value={item.included ? "move" : "stay"}
              disabled={savingId === item.workload_id || op.status !== "awaiting_approval" || !item.destination_node_id || !isAdmin}
              onChange={(e) => changeOptionalIncluded(item, e.target.value === "move")}
              className="w-44 shrink-0 bg-surface2 border border-border rounded px-1.5 py-1 text-xs text-left disabled:opacity-50"
            >
              <option value="stay">{item.currently_running ? "Shut down in place" : "Leave stopped"}</option>
              <option value="move">{item.currently_running ? "Migrate instead" : "Relocate"}</option>
            </select>
            {item.included && item.currently_running && (
              <select
                value={item.transport || "live"}
                disabled={savingId === item.workload_id || op.status !== "awaiting_approval" || !isAdmin}
                onChange={(e) => changeOptionalTransport(item, e.target.value as "live" | "offline")}
                title="Live keeps the guest running throughout. Offline shuts it down first, copies, then powers it back on. Available even when the destination is flagged -- the operator should still be able to choose live vs. shutdown migration even when memory headroom is insufficient."
                className="w-40 shrink-0 bg-surface2 border border-border rounded px-1.5 py-1 text-xs text-left disabled:opacity-50"
              >
                <option value="live">Live</option>
                <option value="offline">Shutdown + migrate</option>
              </select>
            )}
            {item.included && (
              <select
                value={item.destination_node_id || ""}
                disabled={savingId === item.workload_id || op.status !== "awaiting_approval" || !item.destination_node_id || !isAdmin}
                onChange={(e) => changeOptionalDestination(item, e.target.value)}
                title="A candidate marked with a warning was flagged (e.g. tight on headroom right now) but is still yours to pick if you're accepting that."
                className="w-52 shrink-0 bg-surface2 border border-border rounded px-1.5 py-1 text-xs text-left disabled:opacity-50"
              >
                {!item.destination_node_id && <option value="">no destination available</option>}
                {(item.candidates || []).map((c) => (
                  <option key={c.node_id} value={c.node_id}>
                    {c.node_name}
                    {c.blocked ? ` — ⚠ ${c.blocking_reasons[0] || "flagged"}` : ` (score ${c.score})`}
                  </option>
                ))}
              </select>
            )}
          </div>
        ))}
      </div>
      {saveError && <div className="px-2 py-1.5 text-xs text-bad border-t border-border">{saveError}</div>}
    </div>
  );
}

export default function OperationCard({
  op,
  pending,
  onApprove,
  onUpdated,
}: {
  op: Operation;
  pending: boolean;
  onApprove: () => void;
  onUpdated?: (updated: Operation) => void;
}) {
  const progress = op.progress;
  const elapsedSeconds = op.started_at ? Math.round((Date.now() - new Date(op.started_at).getTime()) / 1000) : null;
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  // Balance Load / Bulk Migrate with every line set to "Don't move" has nothing to run.
  const rebalancePlan = ((op.dry_run_result as Record<string, unknown> | null)?.migrate_plan as MigratePlanItem[] | undefined) || [];
  const nothingToMove =
    op.operation_type_id === "cluster.rebalance" && rebalancePlan.length > 0 && rebalancePlan.every((i) => i.transport === "skip");

  return (
    <div className="border border-border rounded-lg p-4 bg-surface2/40">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-medium text-text">{operationSummary(op)}</div>
        <StatusBadge status={op.status} />
      </div>

      {op.stage && (
        <div className="text-xs text-muted mb-2">
          {STAGE_LABELS[op.stage] || op.stage}
          {elapsedSeconds != null && op.status !== "completed" && op.status !== "failed" && <> · {elapsedSeconds}s elapsed</>}
        </div>
      )}

      {op.status === "monitoring" && (
        <div className="mb-3">
          {op.operation_type_id === "vm.live_migrate" ? (
            <MigrationBars op={op} />
          ) : progress && progress.pct != null ? (
            <>
              <div className="h-1.5 rounded-full bg-border overflow-hidden">
                <div className="h-full rounded-full bg-proxmox transition-all" style={{ width: `${Math.min(100, progress.pct)}%` }} />
              </div>
              <div className="text-xs text-muted mt-1">
                {formatBytes(progress.transferred_bytes)} of {formatBytes(progress.total_bytes)} transferred ({progress.pct}%)
                {progress.rate_bytes_per_sec ? ` · ${formatBytes(progress.rate_bytes_per_sec)}/s` : ""}
              </div>
            </>
          ) : (
            <div className="text-xs text-muted italic">Waiting for PVE to report migration progress…</div>
          )}
        </div>
      )}

      {op.status === "awaiting_approval" && PLAN_KEY_BY_TYPE[op.operation_type_id] && (
        <MigratePlanEditor op={op} planKey={PLAN_KEY_BY_TYPE[op.operation_type_id]} onUpdated={(updated) => onUpdated?.(updated)} />
      )}

      {op.dry_run_result && op.dry_run_result.reasons.length > 0 && (
        <ul className="text-xs text-muted list-disc pl-4 mb-2 space-y-0.5">
          {op.dry_run_result.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}

      {op.blocking_safety_rules && op.blocking_safety_rules.length > 0 && (
        <div className="text-xs text-bad mb-2">Blocked by: {op.blocking_safety_rules.join(", ")}</div>
      )}

      {(op.operation_type_id === "host.update" || op.operation_type_id === "host.reboot") && op.status !== "awaiting_approval" && (
        <OperationLogPanel operationId={op.id} active={isInFlight(op.status)} />
      )}

      {op.operation_type_id === "host.update" && op.dry_run_result?.planned_packages != null && (
        <HostUpdatePlan result={op.dry_run_result} />
      )}

      {op.operation_type_id === "host.update" && op.status === "completed" && op.verification_result && (
        <HostUpdateVerification result={op.verification_result} />
      )}

      {(op.status === "awaiting_approval" || (isInFlight(op.status) && COOPERATIVE_CANCEL_TYPES.has(op.operation_type_id))) && (
        <div className="flex items-center gap-2">
          {op.status === "awaiting_approval" && isAdmin && (
            <button
              onClick={onApprove}
              disabled={pending || nothingToMove}
              className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-warn hover:bg-warn/10 disabled:opacity-50"
            >
              {pending ? "Submitting…" : `Approve & Execute ${operationTypeLabel(op)}`}
            </button>
          )}
          {onUpdated && <CancelButton op={op} onCancelled={onUpdated} />}
        </div>
      )}

      {op.pve_upid && <div className="text-xs text-muted mt-2 font-mono">{op.pve_upid}</div>}

      {op.status === "completed" && (
        <div className="text-sm text-good mt-2">Completed and independently verified against live PVE state.</div>
      )}
      {op.status === "failed" && op.error && <div className="text-sm text-bad mt-2">{op.error}</div>}
    </div>
  );
}

type PlannedPackage = { package: string; current_version: string | null; new_version: string };

export function HostUpdatePlan({ result }: { result: NonNullable<Operation["dry_run_result"]> }) {
  const packages = (result.planned_packages as PlannedPackage[] | undefined) || [];
  const kernel = result.kernel_version as string | undefined;
  const diskFree = result.disk_free_bytes as number | null | undefined;

  return (
    <div className="mb-3 text-xs">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted mb-2">
        {kernel && <span>Current kernel: <span className="text-text font-mono">{kernel}</span></span>}
        {diskFree != null && <span>Disk free: <span className="text-text">{formatBytes(diskFree)}</span></span>}
      </div>
      {packages.length === 0 ? (
        <div className="text-muted italic">No pending packages.</div>
      ) : (
        <details className="border border-border rounded">
          <summary className="px-2 py-1.5 cursor-pointer text-text select-none">
            {packages.length} package{packages.length === 1 ? "" : "s"} planned — click to review before approving
          </summary>
          <div className="max-h-64 overflow-y-auto border-t border-border">
            {packages.map((p, i) => (
              <div key={i} className="flex justify-between px-2 py-1 odd:bg-surface/40 font-mono">
                <span className="text-text">{p.package}</span>
                <span className="text-muted">
                  {p.current_version || "(new)"} <span className="text-accent">→</span> {p.new_version}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function HostUpdateVerification({ result }: { result: Record<string, unknown> }) {
  const rebootRequired = result.reboot_required as boolean | undefined;
  return (
    <div className="mt-2 text-xs space-y-1">
      <Row label="Kernel" value={`${result.before_kernel_version || "?"} → ${result.after_kernel_version || "?"}`} />
      <Row
        label="Packages upgradable"
        value={`${result.before_upgradable_count ?? "?"} → ${result.after_upgradable_count ?? "?"}`}
      />
      <Row label="Reboot required" value={rebootRequired ? "yes" : "no"} />
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted">{label}</span>
      <span className="text-text">{value}</span>
    </div>
  );
}
