"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { Finding, Node, Recommendation, RightsizingAssessment, Workload } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import NotesCell from "@/components/NotesCell";
import ApplyRightsizingForm from "@/components/ApplyRightsizingForm";
import WorkloadLifecycleButtons from "@/components/WorkloadLifecycleButtons";
import { Meter } from "@/components/Gauges";
import { formatBytes } from "@/lib/format";
import { CpuIcon, MemoryIcon } from "@/components/Icons";
import { findingWorkloadIds } from "@/lib/findings";
import { useMe } from "@/lib/useMe";

type WorkloadMetric = { cpu_pct?: number; mem_pct?: number };

/** Throws on a failed save instead of resolving silently -- every select
 * below (and the Notes cell) relies on this to know a PATCH didn't
 * actually take, rather than optimistically assuming success -- a failed
 * save used to look
 * identical to a successful one until the next page refresh. */
async function patchPlacementProfile(workloadId: string, body: Record<string, unknown>): Promise<void> {
  const res = await fetch(`/api/workloads/${workloadId}/placement-profile`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Save failed (${res.status})`);
  }
}

function ProfileSelect({
  workloadId,
  field,
  value,
  options,
  highlightWhen,
  onChanged,
}: {
  workloadId: string;
  field: "sensitivity" | "downtime_tolerance";
  value: string;
  options: string[];
  highlightWhen: string;
  onChanged: (v: string) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  async function change(newValue: string) {
    setSaving(true);
    setError(null);
    try {
      await patchPlacementProfile(workloadId, { [field]: newValue });
      onChanged(newValue);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <select
      className={`bg-surface2 border rounded px-1.5 py-1 text-xs ${error ? "border-bad" : "border-border"} ${value === highlightWhen ? "text-warn" : ""}`}
      value={value}
      disabled={saving || !isAdmin}
      title={error || undefined}
      onChange={(e) => change(e.target.value)}
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

function StoragePreferenceSelect({
  workloadId,
  value,
  onChanged,
}: {
  workloadId: string;
  value: string | null;
  onChanged: (v: string | null) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  async function change(newValue: string) {
    const stored = newValue === "auto" ? null : newValue;
    setSaving(true);
    setError(null);
    try {
      await patchPlacementProfile(workloadId, { storage_preference: stored });
      onChanged(stored);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <select
      className={`bg-surface2 border rounded px-1.5 py-1 text-xs ${error ? "border-bad" : "border-border"}`}
      value={value || "auto"}
      disabled={saving || !isAdmin}
      title={error || undefined}
      onChange={(e) => change(e.target.value)}
    >
      <option value="auto">auto (current)</option>
      <option value="local">local</option>
      <option value="shared">shared</option>
    </select>
  );
}

function PreferredHostSelect({
  workloadId,
  clusterNodes,
  value,
  onChanged,
}: {
  workloadId: string;
  clusterNodes: Node[];
  value: string | null;
  onChanged: (v: string | null) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  async function change(newValue: string) {
    const stored = newValue === "none" ? null : newValue;
    setSaving(true);
    setError(null);
    try {
      await patchPlacementProfile(workloadId, { preferred_node_id: stored });
      onChanged(stored);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <select
      className={`bg-surface2 border rounded px-1.5 py-1 text-xs ${error ? "border-bad" : "border-border"}`}
      value={value || "none"}
      disabled={saving || !isAdmin}
      title={error || undefined}
      onChange={(e) => change(e.target.value)}
    >
      <option value="none">no preference</option>
      {clusterNodes.map((n) => (
        <option key={n.id} value={n.id}>
          {n.name}
        </option>
      ))}
    </select>
  );
}

export default function WorkloadsTable({
  workloads,
  nodes,
  findings = [],
  rightsizing = [],
  rightsizingRecs = [],
}: {
  workloads: Workload[];
  nodes: Node[];
  findings?: Finding[];
  /** Same evidence-backed sizing suggestions as the Rightsizing page's own
   * table -- surfaced here too since Workloads and Rightsizing show a lot
   * of the same information, so the suggestion is
   * visible without leaving this page, without merging the two pages
   * outright. */
  rightsizing?: RightsizingAssessment[];
  rightsizingRecs?: Recommendation[];
}) {
  // Arrived here via a VM name link from the Maintenance page: clicking
  // a VM/node name anywhere should jump to its "opposing"
  // page with that item already selected/highlighted, for quick
  // back-and-forth between the two pages' different per-item actions.
  const searchParams = useSearchParams();
  const highlightWorkloadId = searchParams.get("workload");
  const highlightRef = useRef(false);
  useEffect(() => {
    if (!highlightWorkloadId || highlightRef.current) return;
    highlightRef.current = true;
    document.querySelector(".pyxie-jump-target")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlightWorkloadId]);

  // No longer visible to PVE (deleted/renamed there since the last sync) --
  // kept in the DB rather than hard-deleted (see discovery.py), but hidden
  // from the main list by default: it used to sit in this table forever
  // with a generic "Unknown" status that read as a live monitoring gap
  // rather than "gone," which is what actually caused real confusion about
  // an already-deleted guest. Default to showing them anyway if we jumped
  // here from a link that points straight at one, so that deep link still
  // resolves.
  const missingWorkloads = workloads.filter((w) => w.is_missing);
  const [showRemoved, setShowRemoved] = useState(
    () => !!highlightWorkloadId && missingWorkloads.some((w) => w.id === highlightWorkloadId)
  );
  const visibleWorkloads = showRemoved ? workloads : workloads.filter((w) => !w.is_missing);

  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const nodesByCluster = new Map<string, Node[]>();
  for (const n of nodes) {
    const list = nodesByCluster.get(n.cluster_id) || [];
    list.push(n);
    nodesByCluster.set(n.cluster_id, list);
  }
  const findingsByWorkload = new Map<string, Finding[]>();
  for (const f of findings) {
    for (const wid of findingWorkloadIds(f)) {
      const list = findingsByWorkload.get(wid) || [];
      list.push(f);
      findingsByWorkload.set(wid, list);
    }
  }
  const rightsizingByWorkload = new Map(rightsizing.map((a) => [a.workload_id, a]));
  const recommendationIdByWorkload = new Map(
    rightsizingRecs.filter((r) => r.object_id).map((r) => [r.object_id as string, r.id])
  );
  const [sensitivity, setSensitivity] = useState<Record<string, string>>(
    Object.fromEntries(workloads.map((w) => [w.id, w.sensitivity || "standard"]))
  );
  const [downtimeTolerance, setDowntimeTolerance] = useState<Record<string, string>>(
    Object.fromEntries(workloads.map((w) => [w.id, w.downtime_tolerance || "standard"]))
  );
  const [notes, setNotes] = useState<Record<string, string>>(
    Object.fromEntries(workloads.map((w) => [w.id, w.placement_notes || ""]))
  );
  const [storagePref, setStoragePref] = useState<Record<string, string | null>>(
    Object.fromEntries(workloads.map((w) => [w.id, w.storage_preference]))
  );
  const [preferredNode, setPreferredNode] = useState<Record<string, string | null>>(
    Object.fromEntries(workloads.map((w) => [w.id, w.preferred_node_id]))
  );

  // Live usage, polled every 5s -- same pattern as Dashboard/Nodes/
  // Maintenance now use consistently, on any page that shows it. Config
  // (vCPU/RAM columns above) is separate from actual usage -- this is
  // the "how busy is it right now" pair.
  const [metrics, setMetrics] = useState<Record<string, WorkloadMetric>>({});
  useEffect(() => {
    function refresh() {
      fetch("/api/workloads/latest-metrics")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => data && setMetrics(data))
        .catch(() => {});
    }
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      {missingWorkloads.length > 0 && (
        <label className="flex items-center gap-2 text-xs text-muted mb-2 cursor-pointer w-fit">
          <input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} />
          <span>
            Show removed ({missingWorkloads.length}){" "}
            <span className="text-text/70">-- no longer visible to PVE, kept here for history</span>
          </span>
        </label>
      )}
      <Table
      rows={visibleWorkloads}
      emptyMessage="No workloads discovered yet."
      storageKey="infrastructure-workloads"
      rowClassName={(w) => (w.id === highlightWorkloadId ? "bg-accent/10 pyxie-jump-target" : "")}
      columns={[
        { header: "VMID", render: (w) => w.vmid, sortValue: (w) => w.vmid },
        {
          header: "Name",
          render: (w) => {
            const wFindings = findingsByWorkload.get(w.id);
            return (
              <span className="inline-flex items-center gap-1.5">
                <Link href={`/operations/maintenance?workload=${w.id}`} className="text-accent hover:underline" title="Open on the Maintenance page">
                  {w.name || "—"}
                </Link>
                {wFindings && wFindings.length > 0 && (
                  <span
                    className="text-warn cursor-help"
                    title={`Needs attention:\n${wFindings.map((f) => `• ${f.title}`).join("\n")}`}
                  >
                    ⚠
                  </span>
                )}
              </span>
            );
          },
          sortValue: (w) => w.name,
        },
        { header: "Type", render: (w) => w.type.toUpperCase(), sortValue: (w) => w.type },
        {
          header: "Node",
          render: (w) =>
            nodeById.get(w.node_id) ? (
              <Link href={`/infrastructure/nodes/${w.node_id}`} className="text-accent hover:underline" title="Open on Hosts & Clusters">
                {nodeById.get(w.node_id)!.name}
              </Link>
            ) : (
              "—"
            ),
          sortValue: (w) => nodeById.get(w.node_id)?.name,
        },
        {
          header: "Status",
          render: (w) => <StatusBadge status={w.is_missing ? "unknown" : w.status} />,
          sortValue: (w) => (w.is_missing ? "unknown" : w.status),
        },
        {
          header: "Power",
          tooltip:
            "Start/Shut down/Restart this guest -- same Safety Contract as everywhere else (preview, then confirm). Only the action that matches its current status is clickable. VM-only for now; not yet implemented for containers.",
          render: (w) => (
            <WorkloadLifecycleButtons
              workloadId={w.id}
              workloadName={w.name || `VMID ${w.vmid}`}
              status={w.status}
              disabled={w.type !== "vm" || w.is_missing}
              disabledReason={w.is_missing ? "Not currently visible to PVE" : "Guest lifecycle actions are only implemented for VMs, not containers"}
            />
          ),
        },
        {
          header: "CPU Usage",
          tooltip: "Live, as of the last poll (every 5s) -- distinct from the vCPU column, which is the configured allocation, not actual usage.",
          render: (w) => <Meter value={metrics[w.id]?.cpu_pct ?? null} width={56} />,
          sortValue: (w) => metrics[w.id]?.cpu_pct,
        },
        {
          header: "RAM Usage",
          tooltip: "Live, as of the last poll (every 5s) -- distinct from the RAM column, which is the configured allocation, not actual usage.",
          render: (w) => <Meter value={metrics[w.id]?.mem_pct ?? null} width={56} />,
          sortValue: (w) => metrics[w.id]?.mem_pct,
        },
        {
          header: "vCPU",
          render: (w) => (
            <span className="inline-flex items-center gap-1.5">
              <CpuIcon className="w-4 h-4 text-muted" />
              {w.cpu_cores ?? "—"}
            </span>
          ),
          sortValue: (w) => w.cpu_cores,
          optional: true,
        },
        {
          header: "RAM",
          render: (w) => (
            <span className="inline-flex items-center gap-1.5">
              <MemoryIcon className="w-4 h-4 text-muted" />
              {formatBytes(w.memory_bytes)}
            </span>
          ),
          sortValue: (w) => w.memory_bytes,
          optional: true,
        },
        {
          header: "CPU / RAM",
          tooltip: "Currently allocated vCPU and memory for this workload -- always shown here regardless of whether a resize is suggested.",
          render: (w) => {
            const a = rightsizingByWorkload.get(w.id);
            const vcpu = a?.current_vcpu ?? w.cpu_cores;
            const mem = a?.current_memory_bytes ?? w.memory_bytes;
            return (
              <span className="text-muted whitespace-nowrap">
                {vcpu ?? "—"} vCPU · {formatBytes(mem)}
              </span>
            );
          },
          sortValue: (w) => rightsizingByWorkload.get(w.id)?.current_memory_bytes ?? w.memory_bytes,
          optional: true,
        },
        {
          header: "Resize",
          tooltip:
            "Evidence-backed sizing suggestion from observed usage when one exists (click to preview/apply) -- same as the Rightsizing page. Otherwise, click to adjust CPU/RAM manually. See the Rightsizing page for the full CPU/RAM history behind any suggestion.",
          render: (w) => {
            const a = rightsizingByWorkload.get(w.id);
            const hasSuggestion = !!(a && (a.cpu_suggestion || a.memory_suggestion));
            return (
              <ApplyRightsizingForm
                workloadId={w.id}
                workloadName={w.name || `VMID ${w.vmid}`}
                currentCores={a?.current_vcpu ?? w.cpu_cores}
                currentMemoryBytes={a?.current_memory_bytes ?? w.memory_bytes}
                suggestedCores={a?.cpu_suggestion?.suggested ?? null}
                suggestedMemoryBytes={a?.memory_suggestion?.suggested_bytes ?? null}
                recommendationId={a ? recommendationIdByWorkload.get(a.workload_id) : undefined}
                triggerLabel={
                  hasSuggestion ? (
                    <>
                      {a!.cpu_suggestion && (
                        <div className={a!.cpu_suggestion.direction === "increase" ? "text-bad" : "text-good"}>
                          {a!.cpu_suggestion.current} → {a!.cpu_suggestion.suggested} vCPU
                          {a!.cpu_suggestion.direction === "increase" ? " (under-provisioned)" : ""}
                        </div>
                      )}
                      {a!.memory_suggestion && (
                        <div className={a!.memory_suggestion.direction === "increase" ? "text-bad" : "text-good"}>
                          {formatBytes(a!.memory_suggestion.current_bytes)} → {formatBytes(a!.memory_suggestion.suggested_bytes)}
                          {a!.memory_suggestion.direction === "increase" ? " (under-provisioned)" : ""}
                        </div>
                      )}
                    </>
                  ) : (
                    "Resize…"
                  )
                }
              />
            );
          },
          sortValue: (w) => {
            const a = rightsizingByWorkload.get(w.id);
            if (!a) return 0;
            if (a.cpu_suggestion?.direction === "increase" || a.memory_suggestion?.direction === "increase") return 2;
            if (a.cpu_suggestion || a.memory_suggestion) return 1;
            return 0;
          },
          optional: true,
        },
        {
          header: "HA",
          tooltip:
            "PVE's own HA (High Availability) state for this workload, if enrolled. PyXie's HA/CRS awareness is best-effort, not full parity with PVE's own HA manager: node-affinity rules are checked before migrating, but resource affinity/anti-affinity co-location rules are only flagged as present, not fully resolved -- verify those manually before migrating an HA-enrolled workload.",
          render: (w) => w.ha_state || "—",
          sortValue: (w) => w.ha_state,
          optional: true,
        },
        {
          header: "Tags",
          render: (w) => (w.tags && w.tags.length ? w.tags.join(", ") : "—"),
          sortValue: (w) => (w.tags && w.tags.length ? w.tags.join(", ") : ""),
          optional: true,
        },
        {
          header: "Sensitivity",
          tooltip:
            "Marks a workload as security-sensitive. 'restricted' means this workload will NEVER be placed on a node you've tagged Trust Tier = low (see the Nodes page), even if that node is otherwise the best fit. Leave 'standard' unless this workload actually needs that guarantee.",
          render: (w) => (
            <ProfileSelect
              workloadId={w.id}
              field="sensitivity"
              value={sensitivity[w.id] || "standard"}
              options={["standard", "restricted"]}
              highlightWhen="restricted"
              onChanged={(v) => setSensitivity((s) => ({ ...s, [w.id]: v }))}
            />
          ),
          sortValue: (w) => sensitivity[w.id],
          optional: true,
        },
        {
          header: "Downtime Tolerance",
          tooltip:
            "How much outage this workload can absorb. 'low' = cannot tolerate downtime (critical) -- migrations strongly favor your best/most-trusted nodes for it, and it's blocked from Trust Tier = low nodes just like a restricted workload. 'high' = tolerates downtime easily -- it mostly just goes wherever helps balance the cluster, not to the best hosts. 'standard' is the default, no special weighting.",
          render: (w) => (
            <ProfileSelect
              workloadId={w.id}
              field="downtime_tolerance"
              value={downtimeTolerance[w.id] || "standard"}
              options={["low", "standard", "high"]}
              highlightWhen="low"
              onChanged={(v) => setDowntimeTolerance((s) => ({ ...s, [w.id]: v }))}
            />
          ),
          sortValue: (w) => downtimeTolerance[w.id],
          optional: true,
        },
        {
          header: "Storage Preference",
          tooltip:
            "Where this workload's disk should live when migrated. 'auto' infers from wherever it lives today (already-shared stays shared; already-local targets the destination node's own local storage) -- matching a lab that keeps everything on local SSD by default. Override to 'local' or 'shared' to always relocate there regardless of current placement.",
          render: (w) => (
            <StoragePreferenceSelect
              workloadId={w.id}
              value={storagePref[w.id] ?? null}
              onChanged={(v) => setStoragePref((s) => ({ ...s, [w.id]: v }))}
            />
          ),
          sortValue: (w) => storagePref[w.id] || "auto",
          optional: true,
        },
        {
          header: "Preferred Host",
          tooltip:
            "Which node this workload should default to living on. A soft preference, not a hard pin: it strongly favors this node in every migration/maintenance/evacuate/rebalance recommendation, but never overrides a real hard block (insufficient memory, CPU compatibility, trust tier, affinity rules) -- those still exclude it the same as any other candidate. 'no preference' leaves placement to cluster-balance and tier scoring alone, as today.",
          render: (w) => (
            <PreferredHostSelect
              workloadId={w.id}
              clusterNodes={nodesByCluster.get(w.cluster_id) || []}
              value={preferredNode[w.id] ?? null}
              onChanged={(v) => setPreferredNode((s) => ({ ...s, [w.id]: v }))}
            />
          ),
          sortValue: (w) => nodeById.get(preferredNode[w.id] || "")?.name || "",
          optional: true,
        },
        {
          header: "PyXie Notes",
          tooltip:
            "Free-text, stored only in PyXie -- never synced from or to PVE. Use it to record WHY you set this workload's Sensitivity/Downtime Tolerance a certain way, so other admins (or future-you) have a paper trail, not just a value. Every change is logged in the Audit Log with the old and new text.",
          render: (w) => (
            <NotesCell
              value={notes[w.id] || ""}
              onSave={async (v) => {
                await patchPlacementProfile(w.id, { placement_notes: v });
                setNotes((n) => ({ ...n, [w.id]: v }));
              }}
            />
          ),
          sortValue: (w) => notes[w.id],
          optional: true,
        },
      ]}
      />
    </div>
  );
}
