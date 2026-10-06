"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState, type MouseEvent } from "react";
import type { Operation } from "@/lib/api";
import { OPERATION_TYPE_LABELS, operationTypeLabel } from "@/components/OperationCard";
import StatusBadge from "@/components/StatusBadge";
import { ChecklistIcon, SpinnerIcon, HourglassIcon, HistoryIcon, PinIcon } from "@/components/Icons";
import { onOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

const SECTION_ICONS: Record<string, React.ReactNode> = {
  "In Progress": <SpinnerIcon className="w-4 h-4" />,
  "Awaiting Approval": <HourglassIcon className="w-4 h-4" />,
  "Recent": <HistoryIcon className="w-4 h-4" />,
};

export const PANEL_WIDTH_PX = 320;

const IN_FLIGHT_STATUSES = new Set([
  // "pending"/"dry_run" cover the moment a preview/dry-run was just
  // created and is actually being computed (e.g. SSH'ing into a node to
  // check for updates) -- before this was added, that whole window was
  // invisible: nothing showed until the operation reached its final state.
  "pending", "dry_run",
  "approved", "revalidating", "executing", "monitoring", "verifying",
  // workload.resize's own stage names -- shares the same lifecycle shape
  // (revalidate -> do the thing -> verify) but names its middle stages
  // more specifically than the generic executing/monitoring pair.
  "shutting_down", "reconfiguring", "starting_up",
  // node.enter_maintenance / maintenance.run / node.evacuate's own batch
  // stage while it's working through their migrate_plan/shutdown_plan --
  // missing before 2026-09-13, so a multi-VM evacuation never showed its
  // own parent row here at all, only whichever single child migration
  // happened to be running at that moment.
  "evacuating",
]);
const TERMINAL_STATUSES = new Set(["completed", "failed", "blocked", "cancelled"]);

// The three operation types that carry a migrate_plan/shutdown_plan and
// execute it one item at a time (see node_maintenance_workflow.py,
// maintenance_workflow.py, evacuation_workflow.py) -- each item only
// becomes its own real vm.live_migrate/workload.shutdown Operation row
// right before it actually starts, so without this, a 5-VM evacuation
// only ever showed the ONE currently-running migration, with nothing
// indicating more were coming -- each item should be listed as in
// progress, even while it's still queued.
const BATCH_PLAN_OPERATION_TYPES = new Set(["node.enter_maintenance", "maintenance.run", "node.evacuate", "cluster.rebalance"]);

type QueuedItem = { key: string; label: string; subject: string; skipped?: boolean };

// node.enter_maintenance/maintenance.run/cluster.rebalance all key their
// plan "migrate_plan" + "completed_migrations"; node.evacuate is the odd
// one out with "plan" + "completed_workload_ids" (see
// evacuation_workflow.py) -- this went unnoticed until now because every
// batch this was tested against happened to be one of the first three
// (2026-09-13: node.evacuate's own queued items were silently never
// showing here at all, reading keys that never existed in its context).
const MIGRATE_PLAN_KEYS: Record<string, { plan: string; completed: string }> = {
  "node.enter_maintenance": { plan: "migrate_plan", completed: "completed_migrations" },
  "maintenance.run": { plan: "migrate_plan", completed: "completed_migrations" },
  "cluster.rebalance": { plan: "migrate_plan", completed: "completed_migrations" },
  "node.evacuate": { plan: "plan", completed: "completed_workload_ids" },
};

function queuedItemsFor(op: Operation, allOps: Operation[]): QueuedItem[] {
  const ctx = (op.context || {}) as Record<string, unknown>;
  const keys = MIGRATE_PLAN_KEYS[op.operation_type_id];
  const migratePlan = keys && Array.isArray(ctx[keys.plan]) ? (ctx[keys.plan] as any[]) : [];
  const shutdownPlan = Array.isArray(ctx.shutdown_plan) ? (ctx.shutdown_plan as any[]) : [];
  const completedMigrations = new Set(keys && Array.isArray(ctx[keys.completed]) ? (ctx[keys.completed] as string[]) : []);
  const completedShutdowns = new Set(Array.isArray(ctx.completed_shutdowns) ? (ctx.completed_shutdowns as string[]) : []);

  // A plan item stops being "queued" the moment its own real operation row
  // exists (whether that row is still running or already terminal) --
  // this only ever represents items that haven't even started yet.
  const hasOwnOperation = (workloadId: string) =>
    allOps.some((c) => c.parent_operation_id === op.id && c.workload_id === workloadId);

  const items: QueuedItem[] = [];
  for (const item of migratePlan) {
    // "Don't move" lines (Balance Load / Bulk Migrate) never run: listed as Skipped, not queued.
    if (item.transport === "skip") {
      items.push({ key: `${op.id}:skip:${item.workload_id}`, label: "Live Migration", subject: item.name || (item.vmid ? `vmid ${item.vmid}` : "?"), skipped: true });
      continue;
    }
    if (completedMigrations.has(item.workload_id) || hasOwnOperation(item.workload_id)) continue;
    items.push({ key: `${op.id}:mig:${item.workload_id}`, label: "Live Migration", subject: item.name || (item.vmid ? `vmid ${item.vmid}` : "?") });
  }
  for (const item of shutdownPlan) {
    if (completedShutdowns.has(item.workload_id) || hasOwnOperation(item.workload_id)) continue;
    items.push({ key: `${op.id}:sd:${item.workload_id}`, label: "Guest Shutdown", subject: item.name || (item.vmid ? `vmid ${item.vmid}` : "?") });
  }
  return items;
}

// How long a finished task stays in "Recent" before it falls off the panel
// on its own -- still fully findable via Audit Log / the operations API,
// just no longer cluttering the everyday view.
const RECENT_MAX_AGE_MS = 24 * 60 * 60 * 1000;

// The standalone Migrations page retired 2026-09-11 -- live migrations
// (Balance Load, the Workloads "Move" action, or anything else) now show
// up on Maintenance's own operations log alongside everything else.
function pageFor(operationTypeId: string): string {
  // This used to route
  // EVERY operation type to Maintenance, including backup-membership
  // operations that live on the Protection page -- clicking through
  // landed an operator somewhere that had no idea what they were looking
  // at, undermining the preview-first safety pattern.
  if (operationTypeId === "protection.backup_membership") {
    return "/operations/protection";
  }
  if (operationTypeId === "workload.network_vlan_change") {
    return "/infrastructure/network";
  }
  return "/operations/maintenance";
}

function subjectFor(op: Operation): string {
  const r = op.dry_run_result;
  if (op.operation_type_id === "vm.live_migrate") {
    return r?.workload_name || (r?.vmid ? `vmid ${r.vmid}` : r?.node || "?");
  }
  if (["workload.shutdown", "workload.start", "workload.force_stop", "workload.resize", "workload.network_vlan_change"].includes(op.operation_type_id)) {
    return r?.workload_name || (r?.vmid ? `vmid ${r.vmid}` : r?.node || "?");
  }
  if (op.operation_type_id === "cluster.rebalance") {
    // Not tied to one node -- dry_run_result.node is always null here.
    const count = ((r?.migrate_plan as { transport?: string }[] | undefined) || []).filter((i) => i.transport !== "skip").length;
    return `${count} workload${count === 1 ? "" : "s"}`;
  }
  if (op.operation_type_id === "protection.backup_membership") {
    // Not tied to a node or workload either -- falls through to the
    // generic r?.node fallback below otherwise, which is always null
    // here too, rendering as a bare "?".
    return (r?.job_id as string | undefined) || "backup job";
  }
  return r?.node || "?";
}

function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

/** "Oct 2, 8:32:17 AM" in the viewer's own time zone. */
function startTimeText(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString([], {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit",
  });
}

function ProgressBar({ pct }: { pct: number | null | undefined }) {
  if (pct != null) {
    return (
      <div className="h-1.5 rounded-full bg-border overflow-hidden mt-1.5">
        <div className="h-full bg-proxmox transition-all" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
      </div>
    );
  }
  // No byte-level telemetry for this operation type (most W2-W6 stages) --
  // an indeterminate bar still says "something is happening" honestly,
  // without faking a percentage PyXie doesn't actually have.
  return (
    <div className="h-1.5 rounded-full bg-border overflow-hidden mt-1.5 relative">
      <div className="absolute inset-y-0 w-1/3 bg-proxmox rounded-full animate-[taskpanel-indeterminate_1.3s_ease-in-out_infinite]" />
    </div>
  );
}

function TaskRow({ op, onApproved, isAdmin }: { op: Operation; onApproved: () => void; isAdmin: boolean }) {
  const [approving, setApproving] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const inFlight = IN_FLIGHT_STATUSES.has(op.status);
  const dismissable = op.status === "awaiting_approval" || TERMINAL_STATUSES.has(op.status);

  async function approve(e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    setApproving(true);
    try {
      const res = await fetch(`/api/operations/${op.id}/approve`, { method: "POST" });
      if (res.ok) onApproved();
    } finally {
      setApproving(false);
    }
  }

  async function dismiss(e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDismissing(true);
    try {
      const res = await fetch(`/api/operations/${op.id}/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dismissed: true }),
      });
      if (res.ok) onApproved();
    } finally {
      setDismissing(false);
    }
  }

  return (
    <a
      href={pageFor(op.operation_type_id)}
      className="block px-3 py-2 hover:bg-surface2/60 border-b border-border/60"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-text truncate">
          {operationTypeLabel(op)}
        </span>
        <StatusBadge status={op.status} />
      </div>
      <div className="flex items-center justify-between gap-2 mt-0.5">
        <span className="text-[11px] text-muted truncate">{subjectFor(op)}</span>
        <span className="text-[11px] text-muted shrink-0">
          {relativeTime(op.completed_at || op.started_at || op.created_at)}
        </span>
      </div>
      {(op.started_at || op.created_at) && (
        <div
          className="flex items-center justify-between gap-2 mt-0.5"
          title={[
            op.started_at ? `Started ${new Date(op.started_at).toLocaleString()}` : `Requested ${new Date(op.created_at).toLocaleString()}`,
            op.initiated_by_email ? `Initiated by ${op.initiated_by_email}` : op.initiated_by ? `Initiated by ${op.initiated_by}` : "",
            op.approver_email ? `Approved by ${op.approver_email}` : "",
          ].filter(Boolean).join("\n")}
        >
          <span className="text-[11px] text-muted truncate">{op.initiated_by ? `by ${op.initiated_by}` : ""}</span>
          <span className="text-[11px] text-muted shrink-0 tabular-nums">
            {op.started_at ? "" : "requested "}
            {startTimeText(op.started_at || op.created_at)}
          </span>
        </div>
      )}
      {op.approver && op.approver !== op.initiated_by && (
        <div className="text-[11px] text-muted truncate mt-0.5">approved by {op.approver}</div>
      )}
      {inFlight && <ProgressBar pct={op.progress?.pct ?? null} />}
      {op.status === "failed" && op.error && <div className="text-[11px] text-bad mt-1">{op.error}</div>}
      {op.status === "awaiting_approval" && isAdmin && (
        <div className="mt-1.5 flex items-center gap-2">
          <button
            onClick={approve}
            disabled={approving}
            className="px-2 py-1 rounded text-[11px] font-medium bg-black text-white border border-proxmox hover:bg-proxmox/10 disabled:opacity-50"
          >
            {approving ? "Approving…" : "Approve & Execute"}
          </button>
          <button
            onClick={dismiss}
            disabled={dismissing}
            title="Not actually needed -- hide this without approving it. Never deletes the record."
            className="px-2 py-1 rounded text-[11px] font-medium text-muted hover:text-text hover:bg-surface2/60 disabled:opacity-50"
          >
            {dismissing ? "…" : "Dismiss"}
          </button>
        </div>
      )}
      {dismissable && op.status !== "awaiting_approval" && isAdmin && (
        <button
          onClick={dismiss}
          disabled={dismissing}
          className="mt-1 text-[11px] text-muted hover:text-text hover:underline"
        >
          {dismissing ? "…" : "Dismiss"}
        </button>
      )}
    </a>
  );
}

function QueuedRow({ label, subject, by, since, skipped }: { label: string; subject: string; by?: string | null; since?: string | null; skipped?: boolean }) {
  // A queued step has no row of its own yet, so it inherits who asked and when
  // from the operation that owns the plan -- enough to spot one that is stuck.
  return (
    <div
      className="block px-3 py-2 border-b border-border/60 opacity-70"
      title={[since ? `Queued since ${new Date(since).toLocaleString()}` : "", by ? `Requested by ${by}` : ""].filter(Boolean).join("\n")}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-text truncate">{label}</span>
        {skipped ? (
          <span className="text-[10px] uppercase tracking-wide text-muted font-semibold border border-border rounded px-1.5 py-0.5">Skipped</span>
        ) : (
          <StatusBadge status="queued" />
        )}
      </div>
      <div className="flex items-center justify-between gap-2 mt-0.5">
        <span className="text-[11px] text-muted truncate">{subject}</span>
        <span className="text-[11px] text-muted shrink-0">{skipped ? "set to Don't move" : "waiting on prior step"}</span>
      </div>
      {(by || since) && !skipped && (
        <div className="flex items-center justify-between gap-2 mt-0.5">
          <span className="text-[11px] text-muted truncate">{by ? `by ${by}` : ""}</span>
          <span className="text-[11px] text-muted shrink-0 tabular-nums">{since ? `queued ${startTimeText(since)}` : ""}</span>
        </div>
      )}
    </div>
  );
}

function Section({
  title, ops, emptyText, onApproved, divider, extraCount = 0, renderExtra, isAdmin,
}: {
  title: string; ops: Operation[]; emptyText: string; onApproved: () => void; divider?: boolean;
  extraCount?: number; renderExtra?: (op: Operation) => React.ReactNode; isAdmin: boolean;
}) {
  const totalCount = ops.length + extraCount;
  return (
    <div className={`mb-3 ${divider ? "mt-5 pt-4 border-t border-border" : ""}`}>
      <div className="px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider text-proxmox flex items-center justify-between">
        <span className="flex items-center gap-1.5">
          {SECTION_ICONS[title]}
          {title}
        </span>
        {totalCount > 0 && <span>{totalCount}</span>}
      </div>
      {totalCount === 0 ? (
        <div className="px-3 py-2 text-xs text-muted italic">{emptyText}</div>
      ) : (
        <div>
          {ops.map((op) => (
            <div key={op.id}>
              <TaskRow op={op} onApproved={onApproved} isAdmin={isAdmin} />
              {renderExtra?.(op)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function TaskPanel({
  open,
  setOpen,
  pinned,
  setPinned,
  locked = false,
}: {
  open: boolean;
  setOpen: (v: boolean) => void;
  pinned: boolean;
  setPinned: (v: boolean) => void;
  /** Permanently docked open: no pin or close controls (see AppChrome). */
  locked?: boolean;
}) {
  const pathname = usePathname();
  const [operations, setOperations] = useState<Operation[]>([]);
  const me = useMe();
  const isAdmin = me?.is_admin === true;

  function refresh() {
    fetch("/api/operations?limit=100")
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => Array.isArray(data) && setOperations(data))
      .catch(() => {});
  }

  useEffect(() => {
    if (pathname === "/login") return;
    refresh();
    const interval = setInterval(refresh, 5000);
    // Also refresh the instant something happens elsewhere (a preview
    // fired, an approve/dismiss clicked) rather than waiting up to 5s for
    // the next tick -- otherwise a fast action (e.g. a quick update check)
    // can be created and finish between polls and never visibly appear.
    const unsubscribe = onOperationsChanged(refresh);
    return () => {
      clearInterval(interval);
      unsubscribe();
    };
  }, [pathname]);

  if (pathname === "/login") return null;

  const inFlight = operations.filter((o) => IN_FLIGHT_STATUSES.has(o.status) && !o.dismissed);
  const awaitingApproval = operations.filter((o) => o.status === "awaiting_approval" && !o.dismissed);
  // "Recent" is a quick glance at what just happened, not a history browser
  // -- anything older than this falls off the panel on its own (never
  // deleted, still dismissable, and always in full in the Audit Log /
  // GET /api/operations for anyone who needs to actually look it up).
  const recentCutoff = Date.now() - RECENT_MAX_AGE_MS;
  const recent = operations
    .filter((o) => TERMINAL_STATUSES.has(o.status) && !o.dismissed)
    .filter((o) => new Date(o.completed_at || o.updated_at).getTime() >= recentCutoff)
    .sort((a, b) => new Date(b.completed_at || b.updated_at).getTime() - new Date(a.completed_at || a.updated_at).getTime())
    .slice(0, 5);

  const queuedByParentId: Record<string, QueuedItem[]> = {};
  let queuedCount = 0;
  for (const op of inFlight) {
    if (!BATCH_PLAN_OPERATION_TYPES.has(op.operation_type_id)) continue;
    const items = queuedItemsFor(op, operations);
    if (items.length) {
      queuedByParentId[op.id] = items;
      queuedCount += items.filter((q) => !q.skipped).length;
    }
  }

  const badgeCount = inFlight.length + awaitingApproval.length + queuedCount;

  return (
    <>
      <style>{`@keyframes taskpanel-indeterminate { 0% { left: -33%; } 100% { left: 100%; } }`}</style>

      {/* Collapsed edge tab -- hidden once pinned open, since the panel itself is always visible then */}
      {!(pinned && open) && (
        <button
          onClick={() => setOpen(!open)}
          className={`fixed top-1/2 -translate-y-1/2 right-0 z-40 flex flex-col items-center gap-1 rounded-l-lg border border-border bg-surface px-1.5 py-3 shadow-lg transition-transform ${
            open ? "translate-x-full" : "translate-x-0"
          }`}
          aria-label="Toggle task panel"
        >
          <span className="text-[10px] uppercase tracking-wider text-proxmox font-bold [writing-mode:vertical-rl]">Tasks</span>
          {badgeCount > 0 && <span className="text-[11px] font-bold text-proxmox leading-none">{badgeCount}</span>}
        </button>
      )}

      {/* Backdrop only for the temporary (unpinned) peek -- pinned stays open without dimming the page */}
      {open && !pinned && <div className="fixed inset-0 z-30 bg-black/20" onClick={() => setOpen(false)} />}

      {/* Sliding panel */}
      <div
        className="fixed top-0 right-0 z-40 h-screen bg-surface border-l border-border shadow-2xl transition-transform duration-200 ease-out flex flex-col"
        style={{ width: PANEL_WIDTH_PX, transform: open ? "translateX(0)" : "translateX(100%)" }}
      >
        <div className="px-4 py-4 border-b border-border flex items-center justify-between shrink-0">
          <div className="text-sm font-bold text-white uppercase tracking-wide flex items-center gap-2">
            <ChecklistIcon className="w-4 h-4" />
            Tasks
          </div>
          {!locked && (
          <div className="flex items-center gap-3">
            <button
              onClick={() => setPinned(!pinned)}
              className={`${pinned ? "text-proxmox" : "text-muted hover:text-text"}`}
              title={pinned ? "Unpin (overlay mode)" : "Pin open (reserves space, doesn't cover the page)"}
              aria-label="Toggle pin"
            >
              <PinIcon filled={pinned} />
            </button>
            <button onClick={() => setOpen(false)} className="text-muted hover:text-text text-sm">
              close
            </button>
          </div>
          )}
        </div>
        {/* In Progress/Awaiting Approval stay pinned to the top; Recent
            (now capped at 5) is pushed to the bottom of the panel via
            mt-auto instead of just trailing wherever the top sections end
            -- when there's little going on, Recent shouldn't float in the
            middle of empty space. Falls back to normal
            in-flow stacking (scrollable) once content is tall enough to
            fill the panel anyway. */}
        <div className="flex-1 flex flex-col overflow-y-auto py-2">
          <div>
            <Section
              title="In Progress"
              ops={inFlight}
              emptyText="Nothing running right now."
              onApproved={refresh}
              extraCount={queuedCount}
              renderExtra={(op) =>
                queuedByParentId[op.id]?.map((q) => (
                  <QueuedRow key={q.key} label={q.label} subject={q.subject} skipped={q.skipped} by={op.initiated_by} since={op.approved_at || op.started_at || op.created_at} />
                ))
              }
              isAdmin={isAdmin}
            />
            <Section
              title="Awaiting Approval"
              ops={awaitingApproval}
              emptyText="Nothing waiting on you."
              onApproved={refresh}
              divider
              isAdmin={isAdmin}
            />
          </div>
          <div className="mt-auto">
            <Section
              title="Recent"
              ops={recent}
              emptyText="No recent activity."
              onApproved={refresh}
              divider
              isAdmin={isAdmin}
            />
          </div>
        </div>
      </div>
    </>
  );
}
