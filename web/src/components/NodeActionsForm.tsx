"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { HostMaintenanceStatus, Node, Operation, StorageItem } from "@/lib/api";
import OperationCard from "@/components/OperationCard";
import DefaultStorageSelect from "@/components/DefaultStorageSelect";
import { Table } from "@/components/Table";
import { Meter } from "@/components/Gauges";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";
import { isInFlight } from "@/lib/operationStatus";
import { formatBytes, isOverprovisioned, overprovisionedPct } from "@/lib/format";
import { WrenchIcon, PlugIcon, PackageIcon, ClockIcon, ChecklistIcon, MigrateIcon } from "@/components/Icons";

// "Wx" stage numbers are an internal build-tracking label, not something an
// end user should need to decode -- these describe the actual outcome
// instead. Every action here previews first (the section subtitle already
// says so), so the button labels themselves don't repeat "Preview:" --
// that got repetitive across six buttons and read as clutter.
const ACTIONS = [
  {
    id: "enter-maintenance",
    key: "node-maintenance/enter",
    label: "Enter Maintenance Mode",
    description: "Evacuates the node (live-migrates what can move, gracefully shuts down what can't) and marks it out of service. Required before applying updates or rebooting -- for any reason, patching or not. Checking for updates never requires this.",
    icon: WrenchIcon,
    group: "mode" as const,
  },
  {
    id: "exit-maintenance",
    key: "node-maintenance/exit",
    label: "Exit Maintenance Mode",
    description: "Restarts anything this node's maintenance entry shut down and marks it available again. Does not migrate anything back onto it -- workloads moved off stay wherever they were sent.",
    icon: PlugIcon,
    group: "mode" as const,
  },
  {
    id: "reboot-node",
    key: "host-reboots",
    label: "Reboot Node",
    description: "Checks whether it's currently safe to reboot (quorum, active migrations, etc.) -- the node doesn't restart until you approve it.",
    icon: ClockIcon,
    group: "mode" as const,
  },
  {
    id: "check-updates",
    key: "host-updates",
    label: "Check for Updates",
    description: "Shows which package updates are available on the selected node(s). A host not yet connected for patching is read through the PVE API (review only). A host with updates pending can then be applied; one that is up to date needs nothing.",
    icon: PackageIcon,
    group: "action" as const,
  },
  {
    // Same underlying dry-run as "Check for Updates" (a live apt-get
    // update, not cached data -- see host_update_workflow.py), but skips
    // the manual approval click and installs immediately whenever the
    // check comes back clean -- Apply Updates should actually run the
    // apt-install, not just preview. A result that comes back blocked is NOT
    // auto-approved -- it's left exactly as "Check for Updates" would
    // show it, since that's a real safety check refusing to proceed, not
    // an extra click to skip.
    id: "apply-updates",
    key: "host-updates",
    label: "Apply Updates",
    description: "Checks live and installs immediately if nothing blocks it -- no separate approval click. Still fails safe: a real blocker (quorum, disk headroom, etc.) stops it just like every other action here.",
    icon: PackageIcon,
    group: "action" as const,
  },
  {
    id: "full-maintenance",
    key: "maintenance-runs",
    label: "Full Maintenance",
    description: "Evacuate, update, and reboot in one run -- each step is still reviewed and approved individually before it happens.",
    caption: "One combined, reviewed cycle: enter maintenance mode, check for updates, apply them, reboot, exit maintenance mode -- then either restore every VM back to this node, or leave it empty (your choice below).",
    icon: ChecklistIcon,
    group: "action" as const,
  },
];

export default function NodeActionsForm({
  nodes,
  hostMaintenanceByNode: hostMaintenance,
  storage,
  defaultStorageByNode,
  initialNodeId,
  initialActionKey,
  onSelectionChange,
}: {
  // Both live-refreshed by the parent (MaintenanceWorkspace) now, not here
  // -- a real gap found live, the Nodes/Maintenance pages felt like they
  // blocked on "pulling every node" before showing anything. Root cause,
  // measured directly: host-maintenance-status (the SSH probe behind
  // Reboot Required) takes 1-3+ SECONDS PER NODE, and it used to be
  // server-fetched before either page would render at all. Lifting the
  // live state to one shared owner (rather than this component and the
  // page both independently fetching it) means it fetches exactly once,
  // client-side, non-blocking, after the page shell has already painted.
  nodes: Node[];
  hostMaintenanceByNode: Record<string, HostMaintenanceStatus | null>;
  storage: StorageItem[];
  defaultStorageByNode: Record<string, string | null>;
  initialNodeId?: string;
  initialActionKey?: string;
  onSelectionChange?: (ids: string[]) => void;
}) {
  // Each node's own local storage pools, most-free-space first -- same
  // heuristic recommend_storage_for_candidate() itself uses server-side
  // for "auto" (2026-09-12: this used to only ever show that auto pick as
  // read-only display; now it's a real selector, same
  // placement.default_storage_id policy the Nodes page writes).
  const localStorageByNode = useMemo(() => {
    const out: Record<string, StorageItem[]> = {};
    for (const s of storage) {
      if (s.scope !== "node-local" || !s.node_id) continue;
      (out[s.node_id] ||= []).push(s);
    }
    for (const list of Object.values(out)) {
      list.sort((a, b) => (b.capacity_bytes ?? 0) - (b.used_bytes ?? 0) - ((a.capacity_bytes ?? 0) - (a.used_bytes ?? 0)));
    }
    return out;
  }, [storage]);
  // The cluster-shared storage is a valid default too, not local-only --
  // some deployments run shared-first, so it needs to be pickable here
  // just like a specific local pool.
  const sharedStorage = useMemo(() => storage.filter((s) => s.scope === "cluster-shared"), [storage]);
  const [defaultStorage, setDefaultStorage] = useState<Record<string, string | null>>(defaultStorageByNode);
  const [selectedNodeIds, setSelectedNodeIdsState] = useState<string[]>(initialNodeId ? [initialNodeId] : []);
  function setSelectedNodeIds(updater: string[] | ((prev: string[]) => string[])) {
    setSelectedNodeIdsState((prev) => {
      const next = typeof updater === "function" ? (updater as (p: string[]) => string[])(prev) : updater;
      onSelectionChange?.(next);
      return next;
    });
  }
  const [ops, setOps] = useState<Operation[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Full Maintenance only -- reboot_required is Stage W4's own real,
  // post-apply determination (did the patched packages actually need one),
  // which is right most of the time but not the only reason to want a
  // reboot in this same reviewed evacuate/patch/reboot/restore cycle --
  // an operator may want to force a reboot in a Full Maintenance run
  // even when the kernel wasn't updated.
  const [forceReboot, setForceReboot] = useState(false);
  // Full Maintenance only -- the existing behavior migrates every
  // live-migrated VM back to this node and powers the shutdown-in-place
  // ones back on once the patch/reboot verifies clean. Checking this
  // skips all of that: maintenance mode still ends, but nothing comes
  // back or restarts, leaving the node empty -- full maintenance needs
  // the option to return VMs back or leave it empty.
  const [leaveEmpty, setLeaveEmpty] = useState(false);
  const [balancePending, setBalancePending] = useState(false);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  const allSelected = nodes.length > 0 && selectedNodeIds.length === nodes.length;

  useEffect(() => {
    onSelectionChange?.(selectedNodeIds);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);


  function toggleNode(id: string) {
    setSelectedNodeIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
    setOps([]);
  }

  function toggleAll() {
    setSelectedNodeIds(allSelected ? [] : nodes.map((n) => n.id));
    setOps([]);
  }

  // autoApprove is "Apply Updates" only -- it should actually run the
  // apt-install, not just preview and wait for a second click.
  // Everything else, including "Check for Updates" itself,
  // stays preview-then-manual-approve. The dry-run's own safety checks
  // still run in full; this only skips the extra click for whichever
  // result actually landed in awaiting_approval. Anything that came back
  // blocked is left alone, same as every other action -- auto-approving
  // past a real safety block would defeat the point of having one.
  async function runPreview(actionKey: string, autoApprove: boolean = false) {
    if (selectedNodeIds.length === 0) return;
    setPending(true);
    setError(null);
    setOps([]);
    notifyOperationsChanged(); // the Task Panel can now show "in progress" the moment this fires, not just once it resolves
    try {
      const results = await Promise.all(
        selectedNodeIds.map(async (nodeId) => {
          const res = await fetch(`/api/operations/${actionKey}/dry-run`, {
            method: "POST",
            body: JSON.stringify(
              actionKey === "maintenance-runs"
                ? { node_id: nodeId, force_reboot: forceReboot, restore_after: !leaveEmpty }
                : // "Check for Updates" is a look (no approval, works on hosts not yet
                  // connected for patching); Apply Updates is the same key with autoApprove.
                  actionKey === "host-updates" && !autoApprove
                  ? { node_id: nodeId, check_only: true }
                  : { node_id: nodeId }
            ),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || "Preview failed");
          return data as Operation;
        })
      );
      const final = autoApprove
        ? await Promise.all(
            results.map(async (op) => {
              if (op.status !== "awaiting_approval") return op;
              const res = await fetch(`/api/operations/${op.id}/approve`, { method: "POST" });
              const data = await res.json();
              if (!res.ok) throw new Error(data.error || "Apply failed");
              return data as Operation;
            })
          )
        : results;
      setOps(final);
      notifyOperationsChanged(); // reflect the resolved state right away instead of waiting on the panel's next poll
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  // Doesn't require a node selection (unlike every other action here) --
  // no selection means "evaluate every running VM cluster-wide", matching
  // /api/recommendations/balance-load-plan's own default. When nodes ARE
  // selected, it evaluates only the VMs currently on THOSE nodes as move
  // candidates -- destinations are never restricted to the selection:
  // balance the VMs ON the selected nodes, without confining where they
  // can go.
  async function runBalanceLoad() {
    setBalancePending(true);
    setError(null);
    setOps([]);
    notifyOperationsChanged();
    try {
      const res = await fetch("/api/operations/cluster-rebalance/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_ids: selectedNodeIds.length > 0 ? selectedNodeIds : null }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Balance Load failed");
      setOps([data as Operation]);
      notifyOperationsChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBalancePending(false);
    }
  }

  // Arrived here from a specific "go do X" link elsewhere (e.g. the Node
  // page's Reboot Required banner) -- run that preview immediately instead
  // of making the user click the same button a second time.
  const autoTriggered = useRef(false);
  useEffect(() => {
    if (autoTriggered.current) return;
    if (initialNodeId && initialActionKey && ACTIONS.some((a) => a.key === initialActionKey)) {
      autoTriggered.current = true;
      runPreview(initialActionKey);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialNodeId, initialActionKey]);

  // Multiple nodes can now have an in-flight operation at once, so this
  // polls every op still in flight (not just one), same 2s cadence as
  // useOperationPolling, replacing each in place as updates come in.
  useEffect(() => {
    if (!ops.some((o) => isInFlight(o.status))) return;
    const interval = setInterval(async () => {
      const updated = await Promise.all(
        ops.map(async (o) => {
          if (!isInFlight(o.status)) return o;
          const res = await fetch(`/api/operations/${o.id}`);
          return res.ok ? await res.json() : o;
        })
      );
      setOps(updated);
      // An in-flight op finishing HERE (e.g. exit-maintenance actually
      // clearing Node.maintenance_mode a few seconds after approval)
      // previously only updated this component's own `ops` list -- every
      // other live view (the maintenance-mode banner, etc.) had no signal
      // that anything changed until its own next poll tick. Firing the bus
      // the moment a status actually changes closes that gap -- a real
      // gap found live, the exit-maintenance banner didn't clear on its own.
      if (updated.some((o, i) => o.status !== ops[i]?.status)) {
        notifyOperationsChanged();
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [ops]);

  async function approveOne(op: Operation) {
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

  const modeActions = ACTIONS.filter((a) => a.group === "mode");
  const jobActions = ACTIONS.filter((a) => a.group === "action");

  function ActionButton({ a, variant }: { a: (typeof ACTIONS)[number]; variant: "mode" | "action" | "full" }) {
    if (!isAdmin) return null;
    const Icon = a.icon;
    const styles =
      variant === "mode"
        ? "border-warn hover:bg-warn/10"
        : variant === "full"
        ? "border-proxmox hover:bg-proxmox/10"
        : "border-accent hover:bg-accent/10";
    return (
      <button
        onClick={() => runPreview(a.key, a.id === "apply-updates")}
        disabled={selectedNodeIds.length === 0 || pending}
        title={a.description}
        className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium bg-black text-white border disabled:opacity-50 ${styles}`}
      >
        <Icon className="w-4 h-4" />
        {a.label}
      </button>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="flex items-center gap-2 text-sm mb-1.5 cursor-pointer">
          <input type="checkbox" checked={allSelected} onChange={toggleAll} />
          <span className="font-medium">All nodes</span>
        </label>
        <Table
          rows={nodes}
          emptyMessage="No nodes discovered yet."
          storageKey="maintenance-nodes"
          columns={[
            {
              header: "Select",
              render: (n) => (
                <input type="checkbox" checked={selectedNodeIds.includes(n.id)} onChange={() => toggleNode(n.id)} />
              ),
            },
            {
              header: "Name",
              render: (n) => (
                <span className="flex items-center gap-1.5 flex-wrap">
                  <Link href={`/infrastructure/nodes/${n.id}`} className="text-accent hover:underline" title="Open on Hosts & Clusters">
                    {n.name}
                  </Link>
                  {n.maintenance_mode && (
                    <span
                      className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-warn/20 text-warn uppercase tracking-wide"
                      title={n.maintenance_reason ? `Maintenance mode: ${n.maintenance_reason}` : "In maintenance mode"}
                    >
                      Maintenance
                    </span>
                  )}
                  {isOverprovisioned(n) && (
                    <span
                      className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-warn/20 text-warn uppercase tracking-wide"
                      title={`${formatBytes(n.allocated_memory_bytes)} of ${formatBytes(n.mem_total_bytes)} physical RAM allocated (${overprovisionedPct(n)?.toFixed(0)}%) -- may not have headroom for migrations or failovers`}
                    >
                      Overprovisioned
                    </span>
                  )}
                  {hostMaintenance[n.id]?.reboot_required && (
                    <span
                      className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-bad/20 text-bad uppercase tracking-wide"
                      title="A newer kernel or other change is installed but not yet running -- this node needs a reboot."
                    >
                      Reboot Required
                    </span>
                  )}
                  {!!n.pending_updates && (
                    <span
                      className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-accent/20 text-accent uppercase tracking-wide"
                      title={`${n.pending_updates} package update(s) available`}
                    >
                      {n.pending_updates} Updates
                    </span>
                  )}
                </span>
              ),
              sortValue: (n) => n.name,
            },
            {
              header: "CPU",
              render: (n) => <Meter value={n.cpu_usage_pct} width={56} />,
              sortValue: (n) => n.cpu_usage_pct,
            },
            {
              header: "RAM",
              render: (n) => <Meter value={n.mem_usage_pct} width={56} />,
              sortValue: (n) => n.mem_usage_pct,
            },
            {
              header: "Storage",
              tooltip:
                "Which storage a migration should target on this node -- one of its own local pools, or the cluster-shared storage. 'auto' picks whichever local pool has the most free space; pin a specific one (local or shared) for a consistent, standing choice instead. Same setting as the Nodes page.",
              render: (n) => (
                <DefaultStorageSelect
                  nodeId={n.id}
                  value={defaultStorage[n.id] ?? null}
                  autoResolvesTo={(localStorageByNode[n.id] || [])[0]?.name}
                  options={[
                    ...(localStorageByNode[n.id] || []).map((s) => ({ id: s.id, name: s.name, scope: s.scope })),
                    ...sharedStorage.map((s) => ({ id: s.id, name: s.name, scope: s.scope })),
                  ]}
                  onChanged={(v) => setDefaultStorage((s) => ({ ...s, [n.id]: v }))}
                />
              ),
              sortValue: (n) => defaultStorage[n.id] || "",
            },
          ]}
        />
      </div>

      <div>
        <label className="block text-xs text-muted mb-1.5">Maintenance mode</label>
        <div className="flex flex-wrap gap-2">
          {modeActions.map((a) => (
            <ActionButton key={a.id} a={a} variant="mode" />
          ))}
        </div>
        {isAdmin && selectedNodeIds.length === 0 && (
          <p className="text-xs text-muted mt-1.5">Select one or more nodes above to enable these actions.</p>
        )}
      </div>
      <div>
        <label className="block text-xs text-muted mb-1.5">Actions</label>
        <div className="flex flex-wrap gap-2">
          {jobActions.map((a) => (
            <ActionButton key={a.id} a={a} variant={a.key === "maintenance-runs" ? "full" : "action"} />
          ))}
        </div>
        {isAdmin && selectedNodeIds.length === 0 && (
          <p className="text-xs text-muted mt-1.5">Select one or more nodes above to enable these actions.</p>
        )}
        {jobActions.map((a) =>
          "caption" in a ? (
            <p key={a.id} className="text-xs text-muted mt-1.5">
              <span className="font-medium text-text">{a.label}:</span> {a.caption}
            </p>
          ) : null
        )}
        <label className="flex items-center gap-1.5 text-xs text-muted mt-1.5 cursor-pointer w-fit">
          <input type="checkbox" checked={forceReboot} onChange={(e) => setForceReboot(e.target.checked)} />
          <span>
            Reboot anyway, even if no update needs one <span className="text-text/70">(Full Maintenance only)</span>
          </span>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted mt-1.5 cursor-pointer w-fit">
          <input type="checkbox" checked={leaveEmpty} onChange={(e) => setLeaveEmpty(e.target.checked)} />
          <span>
            Leave the node empty afterward, don't restore VMs{" "}
            <span className="text-text/70">(Full Maintenance only)</span>
          </span>
        </label>
      </div>
      <div>
        {/* Doesn't fit neatly under "Maintenance mode" or the per-node
            "Actions" above -- it's cluster-wide by default, only scoped to
            selected node(s) as an option -- so it gets its own row rather
            than crowding into either. Revisit once there's
            a better home for it. */}
        <label className="block text-xs text-muted mb-1.5">Balance</label>
        {isAdmin && (
        <button
          onClick={runBalanceLoad}
          disabled={balancePending}
          title={
            selectedNodeIds.length > 0
              ? "Evaluate the VMs on the selected node(s) and propose better homes for them anywhere eligible in the cluster."
              : "Evaluate every running VM cluster-wide and propose moves that would improve overall balance."
          }
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium bg-black text-white border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          <MigrateIcon className="w-4 h-4" />
          {balancePending ? "Evaluating…" : "Balance Load"}
        </button>
        )}
        <p className="text-xs text-muted mt-1.5">
          <span className="font-medium text-text">Balance Load:</span> {selectedNodeIds.length > 0
            ? "proposes moving VMs off the selected node(s) onto a better-scoring node elsewhere in the cluster."
            : "proposes moving any VM cluster-wide onto a better-scoring node -- select node(s) above to scope it to just those."}
        </p>
      </div>
      <p className="text-xs text-muted">
        Every action previews first -- current state, blockers if any. Nothing installs, migrates, or
        reboots until you review the result below and click Approve, individually, for each node --
        {" "}
        <span className="font-medium text-text">except Apply Updates</span>, which auto-applies the
        moment its own check comes back clean (see its description above; a real blocker still stops
        it, same as everything else). Checking for updates also refreshes that host&apos;s local
        package metadata (<code>apt-get update</code>) even when nothing else about it changes --
        {" "}
        <span className="font-medium text-text">this is the one action here with any real effect
        before you click Approve</span>.
      </p>

      {error && <div className="text-sm text-bad">{error}</div>}
      {ops.length > 0 && (
        <div className="space-y-3">
          {ops.map((op) => (
            <OperationCard
              key={op.id}
              op={op}
              pending={pending}
              onApprove={() => approveOne(op)}
              onUpdated={(updated) => setOps((prev) => prev.map((o) => (o.id === updated.id ? updated : o)))}
            />
          ))}
        </div>
      )}

    </div>
  );
}
