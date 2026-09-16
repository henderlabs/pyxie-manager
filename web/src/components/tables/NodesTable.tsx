"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import type { HostMaintenanceStatus, Node, StorageItem } from "@/lib/api";
import { Table } from "@/components/Table";
import { NoInfrastructureHint } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import NotesCell from "@/components/NotesCell";
import DefaultStorageSelect from "@/components/DefaultStorageSelect";
import { formatBytes, formatUptime, isOverprovisioned, overprovisionedPct } from "@/lib/format";
import { compareVersions } from "@/lib/version";
import { Meter } from "@/components/Gauges";
import { useMe } from "@/lib/useMe";

/** Throws on a failed save instead of resolving silently -- see
 * WorkloadsTable.tsx's patchPlacementProfile for the same fix, same
 * reasoning. */
async function putPolicy(body: Record<string, unknown>): Promise<void> {
  const res = await fetch("/api/policies", { method: "PUT", body: JSON.stringify(body) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Save failed (${res.status})`);
  }
}

export type TierSuggestion = {
  node_name: string;
  current_performance_tier: string;
  current_trust_tier: string;
  current_notes: string;
  suggested_performance_tier: string;
  suggested_trust_tier: string;
  cpu_cores: number | null;
  cpu_model: string | null;
};

function TierSelect({
  nodeId,
  tierKey,
  value,
  suggested,
  onChanged,
}: {
  nodeId: string;
  tierKey: "performance" | "trust";
  value: string;
  suggested: string;
  onChanged: (value: string) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function change(newValue: string) {
    setSaving(true);
    setError(null);
    try {
      await putPolicy({
        scope_type: "node",
        scope_id: nodeId,
        key: `placement.${tierKey}_tier`,
        value: newValue,
      });
      onChanged(newValue);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <select
        className={`bg-surface2 border rounded px-1.5 py-1 text-xs ${error ? "border-bad" : "border-border"}`}
        value={value}
        disabled={saving || !isAdmin}
        title={error || undefined}
        onChange={(e) => change(e.target.value)}
      >
        <option value="low">low</option>
        <option value="standard">standard</option>
        <option value="high">high</option>
      </select>
      {suggested !== value && (
        <div className="text-[10px] text-muted mt-0.5">suggested: {suggested}</div>
      )}
      {error && <div className="text-bad text-[10px] mt-0.5">{error}</div>}
    </div>
  );
}

function CanaryToggle({ nodeId, checked, onChanged }: { nodeId: string; checked: boolean; onChanged: (v: boolean) => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function change(v: boolean) {
    setSaving(true);
    setError(null);
    try {
      await putPolicy({ scope_type: "node", scope_id: nodeId, key: "rollout.canary", value: v });
      onChanged(v);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <label className="inline-flex items-center gap-1.5 cursor-pointer" title={error || undefined}>
      <input
        type="checkbox"
        checked={checked}
        disabled={saving || !isAdmin}
        onChange={(e) => change(e.target.checked)}
        className={`accent-proxmox ${error ? "outline outline-1 outline-bad" : ""}`}
      />
      {checked && <span className="text-[10px] uppercase tracking-wide text-proxmox font-semibold">canary</span>}
    </label>
  );
}

export default function NodesTable({
  nodes: initialNodes,
  clusterId,
  tierSuggestions,
  canaryByNode,
  storage,
  defaultStorageByNode,
}: {
  nodes: Node[];
  /** Scopes the live poll below to this cluster only, matching the page's
   * own initial server-side filter (`nodes.filter(n => n.cluster_id ===
   * cluster.id)` in infrastructure/nodes/page.tsx). Without this, the 5s
   * refresh replaced `nodes` with the raw unscoped /api/nodes response --
   * dormant with one cluster in this deployment, but would leak every
   * other cluster's nodes into this table 5s after page load the moment
   * a second cluster exists (found in passing 2026-09-15, fixed now). */
  clusterId: string;
  tierSuggestions: Record<string, TierSuggestion>;
  canaryByNode: Record<string, boolean>;
  storage: StorageItem[];
  defaultStorageByNode: Record<string, string | null>;
}) {
  // CPU/RAM (and everything else on Node) polled every 5s -- cheap DB
  // read, same pattern as the Maintenance page. A real gap found live:
  // this page's meters looked frozen next to Maintenance's because this
  // was the one place still using a single server-fetched snapshot with
  // no refresh of its own.
  const [nodes, setNodes] = useState<Node[]>(initialNodes);
  useEffect(() => {
    function refresh() {
      fetch("/api/nodes")
        .then((r) => (r.ok ? r.json() : null))
        .then((data: Node[] | null) => data && setNodes(data.filter((n) => n.cluster_id === clusterId)))
        .catch(() => {});
    }
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [clusterId]);

  // Fetched here, client-side, instead of server-side on the page -- this
  // is a real SSH probe per node (1-3+ seconds each, measured directly
  // 2026-09-12), so the page shell renders first and this fills in a
  // moment later instead of blocking the whole page on it.
  const [hostMaintenanceByNode, setHostMaintenanceByNode] = useState<Record<string, HostMaintenanceStatus | null>>({});
  useEffect(() => {
    Promise.all(
      initialNodes.map(async (n) => {
        try {
          const res = await fetch(`/api/nodes/${n.id}/host-maintenance-status`);
          return [n.id, res.ok ? ((await res.json()) as HostMaintenanceStatus) : null] as const;
        } catch {
          return [n.id, null] as const;
        }
      })
    ).then((entries) => setHostMaintenanceByNode(Object.fromEntries(entries)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [canary, setCanary] = useState<Record<string, boolean>>(canaryByNode);
  const [tiers, setTiers] = useState<Record<string, { performance: string; trust: string }>>(
    Object.fromEntries(
      nodes.map((n) => [
        n.id,
        {
          performance: tierSuggestions[n.id]?.current_performance_tier || "standard",
          trust: tierSuggestions[n.id]?.current_trust_tier || "standard",
        },
      ])
    )
  );
  const [notes, setNotes] = useState<Record<string, string>>(
    Object.fromEntries(nodes.map((n) => [n.id, tierSuggestions[n.id]?.current_notes || ""]))
  );
  const [defaultStorage, setDefaultStorage] = useState<Record<string, string | null>>(defaultStorageByNode);

  // This node's own local storage pools, most-free-space first -- same
  // heuristic recommend_storage_for_candidate() itself uses server-side
  // for "auto." Options for the selector; the first one is what "auto"
  // currently resolves to.
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

  // No longer visible to PVE (decommissioned/removed from the cluster) --
  // kept in the DB rather than hard-deleted, but hidden from the main list
  // by default, same fix as the Workloads page's "Show removed" toggle
  // (2026-09-15) and for the same reason: a generic "Unknown" status read
  // as a live monitoring gap, not "this node is gone."
  const missingNodes = nodes.filter((n) => n.is_missing);
  const [showRemoved, setShowRemoved] = useState(false);
  const visibleNodes = showRemoved ? nodes : nodes.filter((n) => !n.is_missing);

  return (
    <div>
      {missingNodes.length > 0 && (
        <label className="flex items-center gap-2 text-xs text-muted mb-2 cursor-pointer w-fit">
          <input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} />
          <span>
            Show removed ({missingNodes.length}){" "}
            <span className="text-text/70">-- no longer visible to PVE, kept here for history</span>
          </span>
        </label>
      )}
      <Table
      rows={visibleNodes}
      emptyMessage={<NoInfrastructureHint subject="nodes" />}
      storageKey="infrastructure-nodes"
      columns={[
        {
          header: "Name",
          render: (n) => (
            <div className="flex items-center gap-2">
              <Link href={`/infrastructure/nodes/${n.id}`} className="text-accent hover:underline">
                {n.name}
              </Link>
              {canary[n.id] && (
                <span className="text-[10px] uppercase tracking-wide text-proxmox font-semibold border border-proxmox/40 rounded px-1 py-0.5">
                  canary
                </span>
              )}
              {n.maintenance_mode && (
                <span
                  className="text-[10px] uppercase tracking-wide text-warn font-semibold border border-warn/40 rounded px-1 py-0.5"
                  title={n.maintenance_reason ? `Maintenance mode: ${n.maintenance_reason}` : "In maintenance mode"}
                >
                  maintenance
                </span>
              )}
              {isOverprovisioned(n) && (
                <span
                  className="text-[10px] uppercase tracking-wide text-warn font-semibold border border-warn/40 rounded px-1 py-0.5"
                  title={`${formatBytes(n.allocated_memory_bytes)} of ${formatBytes(n.mem_total_bytes)} physical RAM allocated (${overprovisionedPct(n)?.toFixed(0)}%) -- may not have headroom for migrations or failovers`}
                >
                  overprovisioned
                </span>
              )}
            </div>
          ),
          sortValue: (n) => n.name,
        },
        {
          header: "Canary",
          tooltip:
            "Mark this node for staged rollout -- validate maintenance/patching here FIRST before running it against the rest of the cluster. PyXie-only, never synced to/from PVE.",
          render: (n) => (
            <CanaryToggle
              nodeId={n.id}
              checked={!!canary[n.id]}
              onChanged={(v) => setCanary((c) => ({ ...c, [n.id]: v }))}
            />
          ),
          sortValue: (n) => (canary[n.id] ? 1 : 0),
        },
        {
          header: "Status",
          render: (n) => <StatusBadge status={n.is_missing ? "unknown" : n.status} />,
          sortValue: (n) => (n.is_missing ? "unknown" : n.status),
        },
        { header: "CPU", render: (n) => <Meter value={n.cpu_usage_pct} width={64} />, sortValue: (n) => n.cpu_usage_pct },
        { header: "RAM", render: (n) => <Meter value={n.mem_usage_pct} width={64} />, sortValue: (n) => n.mem_usage_pct },
        { header: "Uptime", render: (n) => formatUptime(n.uptime_seconds), sortValue: (n) => n.uptime_seconds, optional: true },
        {
          header: "PVE Version",
          render: (n) => n.pve_version || "—",
          compare: (a, b) => compareVersions(a.pve_version, b.pve_version),
          optional: true,
        },
        {
          header: "Updates",
          tooltip: "null/unknown (shown as —) means PyXie couldn't determine this -- e.g. no maintenance credential configured for this cluster yet -- and is NOT the same as a confirmed 0.",
          render: (n) => (n.pending_updates == null ? "—" : String(n.pending_updates)),
          sortValue: (n) => n.pending_updates ?? -1,
          optional: true,
        },
        {
          header: "Reboot",
          tooltip: "Live over SSH -- only populated for nodes with the host-maintenance wrapper provisioned. Compares the running kernel against the newest installed kernel package.",
          render: (n) => {
            const hm = hostMaintenanceByNode[n.id];
            if (!hm || !hm.provisioned) return <span className="text-muted text-xs">—</span>;
            if (!hm.reachable) return <span className="text-muted text-xs" title={hm.error}>unreachable</span>;
            return hm.reboot_required ? (
              <span className="text-[10px] uppercase tracking-wide text-bad font-semibold border border-bad/40 rounded px-1.5 py-0.5">
                required
              </span>
            ) : (
              <span className="text-xs text-muted">no</span>
            );
          },
          sortValue: (n) => (hostMaintenanceByNode[n.id]?.reboot_required ? 1 : 0),
          optional: true,
        },
        {
          header: "Performance Tier",
          tooltip:
            "How capable this node's hardware is relative to your OTHER nodes (CPU cores/model) -- 'high'/'low' are relative, not an absolute judgment. Migrations for critical (low downtime tolerance) workloads favor 'high' nodes more strongly; tolerant workloads barely care. Set manually -- a hardware-derived suggestion shows below the dropdown when it differs from the current value.",
          render: (n) => (
            <TierSelect
              nodeId={n.id}
              tierKey="performance"
              value={tiers[n.id]?.performance || "standard"}
              suggested={tierSuggestions[n.id]?.suggested_performance_tier || "standard"}
              onChanged={(v) => setTiers((t) => ({ ...t, [n.id]: { ...t[n.id], performance: v } }))}
            />
          ),
          sortValue: (n) => tiers[n.id]?.performance,
        },
        {
          header: "Trust Tier",
          tooltip:
            "How much you trust this node's security posture / operational stability. 'low' HARD-BLOCKS placement of any workload tagged Sensitivity=restricted or Downtime Tolerance=low here, and lowers this node's score for everything else. There's no hardware signal for this -- it's always your manual judgment call.",
          render: (n) => (
            <TierSelect
              nodeId={n.id}
              tierKey="trust"
              value={tiers[n.id]?.trust || "standard"}
              suggested={tierSuggestions[n.id]?.suggested_trust_tier || "standard"}
              onChanged={(v) => setTiers((t) => ({ ...t, [n.id]: { ...t[n.id], trust: v } }))}
            />
          ),
          sortValue: (n) => tiers[n.id]?.trust,
        },
        {
          header: "Default Storage",
          tooltip:
            "Which storage a migration should target on this node -- one of its own local pools, or the cluster-shared storage. 'auto' picks whichever local pool has the most free space; pin a specific one (local or shared) for a consistent, standing choice instead. Same setting as the Maintenance page's Nodes panel.",
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
          optional: true,
        },
        {
          header: "PyXie Notes",
          tooltip:
            "Free-text, stored only in PyXie -- never synced from or to PVE. Use it to record WHY you set this node's tiers a certain way, e.g. 'marked low trust: this node loses quorum membership randomly.' Every change is logged in the Audit Log with the old and new text.",
          render: (n) => (
            <NotesCell
              value={notes[n.id] || ""}
              onSave={async (v) => {
                await putPolicy({ scope_type: "node", scope_id: n.id, key: "placement.notes", value: v });
                setNotes((s) => ({ ...s, [n.id]: v }));
              }}
            />
          ),
          sortValue: (n) => notes[n.id],
        },
      ]}
      />
    </div>
  );
}
