"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { Finding, Node, Recommendation, RightsizingAssessment, StorageItem, Workload } from "@/lib/api";
import { Table, sortRows, type Column, type SortState } from "@/components/Table";
import { NoInfrastructureHint } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import NotesCell from "@/components/NotesCell";
import ApplyRightsizingForm from "@/components/ApplyRightsizingForm";
import WorkloadLifecycleButtons from "@/components/WorkloadLifecycleButtons";
import MigrateWorkloadAction from "@/components/MigrateWorkloadAction";
import { Meter } from "@/components/Gauges";
import { formatBytes, formatUptime } from "@/lib/format";
import { LIVENESS_CLASS, LIVENESS_LABEL, LIVENESS_RANK } from "@/lib/liveness";
import type { Liveness } from "@/lib/workloadDetail";
import { CpuIcon, MemoryIcon } from "@/components/Icons";
import { findingWorkloadIds } from "@/lib/findings";
import { useMe } from "@/lib/useMe";
import { PreferredHostSelect, ProfileSelect, StoragePreferenceSelect, patchPlacementProfile } from "@/components/ProfileSelect";
import { WorkloadRowDetail } from "@/components/WorkloadDetail";

type WorkloadFacts = {
  uptime: number | null;
  lock: string | null;
  template: boolean;
  start_at_boot: boolean | null;
  agent_enabled: boolean | null;
  ostype: string | null;
  disk_bytes: number | null;
  storages: string[];
};

type WorkloadMetric = { cpu_pct?: number; mem_pct?: number; mem_source?: "guest" | "host"; ballooning?: "on" | "off" | "pending"; balloon_min_mb?: number | null };

export default function WorkloadsTable({
  workloads,
  nodes,
  storage = [],
  findings = [],
  rightsizing = [],
  rightsizingRecs = [],
}: {
  workloads: Workload[];
  nodes: Node[];
  /** Destination candidates for the per-row "Migrate" action's storage
   * picker -- same list the Maintenance page's move form uses. */
  storage?: StorageItem[];
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

  // Same match predicate as WorkloadSearchSelect.tsx (the Maintenance
  // page's VM picker) -- case-insensitive substring on name or vmid --
  // so a VM findable there is findable the same way here.
  const [search, setSearch] = useState("");
  const searchQuery = search.trim().toLowerCase();
  const searchedWorkloads = searchQuery
    ? visibleWorkloads.filter((w) => (w.name || "").toLowerCase().includes(searchQuery) || String(w.vmid).includes(searchQuery))
    : visibleWorkloads;

  // Server-rendering all ~130+ rows (each with several interactive
  // per-row components -- Power/Migrate/Resize/notes) was measured at
  // 4+ seconds on cre-pyxie's real fleet size, the actual cause of "slow
  // page loads" (confirmed directly: individual data fetches were all
  // under 100ms, so it was never a data/caching problem). Paginating
  // cuts the render cost proportionally. Sorting is lifted up to here
  // (via Table's exported sortRows(), same logic Table uses internally)
  // and applied BEFORE pagination slices the list, so a sort ranks
  // across the full dataset, not just whichever page happens to be
  // showing -- this is meant to hold up at real enterprise fleet sizes,
  // not just 130 VMs.
  const PAGE_SIZE = 25;
  const [sort, setSort] = useState<SortState>(null);
  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [searchQuery, showRemoved, sort]);

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

  // Config-derived facts (Start at boot, agent, OS, disk) and live uptime/lock for every guest:
  // one cached server call, so the table renders at once and these fill in a moment later.
  const [facts, setFacts] = useState<Record<string, WorkloadFacts>>({});
  useEffect(() => {
    let cancelled = false;
    function refresh() {
      fetch("/api/workloads/facts")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => data?.facts && !cancelled && setFacts(data.facts))
        .catch(() => {});
    }
    refresh();
    const interval = setInterval(refresh, 60000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Is each running VM's QEMU answering? One cached server scan (about 6 s cold for 116 VMs), so the
  // table renders at once and this column fills in afterwards.
  const [liveness, setLiveness] = useState<Record<string, Liveness>>({});
  useEffect(() => {
    let cancelled = false;
    function refresh() {
      fetch("/api/workloads/liveness")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => data?.liveness && !cancelled && setLiveness(data.liveness))
        .catch(() => {});
    }
    refresh();
    const interval = setInterval(refresh, 60000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const columns: Column<Workload>[] = [
        { header: "VMID", render: (w) => w.vmid, sortValue: (w) => w.vmid },
        {
          header: "Name",
          render: (w) => {
            const wFindings = findingsByWorkload.get(w.id);
            return (
              <span className="inline-flex items-center gap-1.5">
                <Link href={`/infrastructure/workloads/${w.id}`} className="text-accent hover:underline" title="Open this workload">
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
          header: "Live Check",
          tooltip:
            "Asks PVE whether each running VM's QEMU is answering. PVE calls a VM 'running' from its process alone, so a VM whose QEMU control socket is hung still looks fine -- but a live migration of it hangs for about 10 minutes and fails. 'Not responding' means power-cycle it from PVE before moving it. Hover a badge for the detail.",
          render: (w) => {
            const l = liveness[w.id];
            if (w.status !== "running" || !l) return <span className="text-muted">—</span>;
            return (
              <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium whitespace-nowrap ${LIVENESS_CLASS[l.state]}`} title={l.detail}>
                {LIVENESS_LABEL[l.state]}
              </span>
            );
          },
          sortValue: (w) => (w.status === "running" && liveness[w.id] ? LIVENESS_RANK[liveness[w.id].state] : 9),
          optional: true,
        },
        {
          header: "Uptime",
          tooltip: "How long this guest has been running, from PVE. Blank while stopped.",
          render: (w) => <span className="text-muted whitespace-nowrap">{w.status === "running" ? formatUptime(facts[w.id]?.uptime) : "—"}</span>,
          sortValue: (w) => (w.status === "running" ? facts[w.id]?.uptime ?? null : null),
          optional: true,
        },
        {
          header: "Start at boot",
          tooltip:
            "PVE's 'Start at boot' option: the host powers this guest on by itself whenever it boots. Shown in amber on a stopped guest, because it will start by itself after a host reboot.",
          render: (w) => {
            const f = facts[w.id]?.start_at_boot;
            if (f == null) return <span className="text-muted">—</span>;
            if (f && w.status !== "running") {
              return <span className="text-warn" title="Stopped now, but it will power on by itself after a host reboot">Yes</span>;
            }
            return f ? "Yes" : <span className="text-muted">No</span>;
          },
          sortValue: (w) => (facts[w.id]?.start_at_boot == null ? null : facts[w.id]?.start_at_boot ? 1 : 0),
          optional: true,
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
          header: "Migrate",
          tooltip:
            "Move this VM to another node in the same cluster -- same preview/approve Safety Contract as the Maintenance page's Move action, just without leaving this page. VM-only; not yet implemented for containers.",
          render: (w) =>
            w.type === "vm" && !w.is_missing ? (
              <MigrateWorkloadAction
                workloadId={w.id}
                workloadName={w.name || `VMID ${w.vmid}`}
                clusterId={w.cluster_id}
                currentNodeId={w.node_id}
                nodes={nodes}
                storage={storage}
              />
            ) : (
              <span className="text-muted">—</span>
            ),
          optional: true,
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
          render: (w) => <Meter value={metrics[w.id]?.mem_pct ?? null} width={56} hostOnly={metrics[w.id]?.mem_source === "host"} />,
          sortValue: (w) => metrics[w.id]?.mem_pct,
        },
        {
          header: "Ballooning",
          tooltip:
            "Whether the VM has the memory-balloon device. Off: PVE cannot see the guest's real memory use, so RAM Usage shows the host-side size (often ~100%). Pending: configured, takes effect after a power-cycle through PVE (not a restart from inside the guest). The number is the guest minimum.",
          render: (w) => {
            const m = metrics[w.id];
            if (w.type !== "vm" || !m?.ballooning) return <span className="text-muted">—</span>;
            if (m.ballooning === "off")
              return <span className="rounded px-1.5 py-0.5 text-xs font-medium bg-red-500/15 text-red-600 dark:text-red-400">Off</span>;
            const min = m.balloon_min_mb ? `min ${formatBytes(m.balloon_min_mb * 1048576)}` : "";
            return m.ballooning === "pending" ? (
              <span className="rounded px-1.5 py-0.5 text-xs font-medium bg-amber-500/15 text-amber-600 dark:text-amber-400" title="Configured, not active until the VM is power-cycled through PVE">
                Pending {min}
              </span>
            ) : (
              <span className="text-xs text-muted whitespace-nowrap">On {min}</span>
            );
          },
          sortValue: (w) => metrics[w.id]?.ballooning,
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
          defaultHidden: true,
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
          header: "Guest Agent",
          tooltip: "Whether the QEMU guest agent is enabled in the VM's PVE config (it can still be enabled but not running inside the guest).",
          render: (w) => {
            const a = facts[w.id]?.agent_enabled;
            return a == null ? <span className="text-muted">—</span> : a ? "Enabled" : <span className="text-muted">Off</span>;
          },
          sortValue: (w) => (facts[w.id]?.agent_enabled == null ? null : facts[w.id]?.agent_enabled ? 1 : 0),
          optional: true,
          defaultHidden: true,
        },
        {
          header: "OS Type",
          tooltip: "The OS type set in the guest's PVE config (l26 = Linux 2.6+, win11, win10, ...).",
          render: (w) => <span className="text-muted">{facts[w.id]?.ostype || "—"}</span>,
          sortValue: (w) => facts[w.id]?.ostype ?? null,
          optional: true,
          defaultHidden: true,
        },
        {
          header: "Disk",
          tooltip: "Total size of the guest's data disks (EFI/TPM state and empty CD-ROMs excluded) and the storage(s) they sit on.",
          render: (w) => {
            const f = facts[w.id];
            if (!f || f.disk_bytes == null) return <span className="text-muted">—</span>;
            return (
              <span className="whitespace-nowrap">
                {formatBytes(f.disk_bytes)} <span className="text-muted">{f.storages.join(", ")}</span>
              </span>
            );
          },
          sortValue: (w) => facts[w.id]?.disk_bytes ?? null,
          optional: true,
          defaultHidden: true,
        },
        {
          header: "Lock",
          tooltip: "A PVE lock on the guest (backup, migrate, snapshot, ...). A lock that never clears usually means a task died part-way.",
          render: (w) => (facts[w.id]?.lock ? <span className="text-warn">{facts[w.id]?.lock}</span> : <span className="text-muted">—</span>),
          sortValue: (w) => facts[w.id]?.lock ?? null,
          optional: true,
          defaultHidden: true,
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
          defaultHidden: true,
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
          defaultHidden: true,
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
          defaultHidden: true,
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
          defaultHidden: true,
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
  ];

  const sortedSearchedWorkloads = sortRows(searchedWorkloads, columns, sort);
  const totalPages = Math.max(1, Math.ceil(sortedSearchedWorkloads.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pagedWorkloads = sortedSearchedWorkloads.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  return (
    <div>
      <div className="flex items-center gap-4 mb-2">
        {missingWorkloads.length > 0 && (
          <label className="flex items-center gap-2 text-xs text-muted cursor-pointer w-fit shrink-0">
            <input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} />
            <span>
              Show removed ({missingWorkloads.length}){" "}
              <span className="text-text/70">-- no longer visible to PVE, kept here for history</span>
            </span>
          </label>
        )}
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name or vmid…"
          className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm w-64"
        />
      </div>
      <Table
        rows={pagedWorkloads}
        controlledSort={sort}
        onSortChange={setSort}
        emptyMessage={searchQuery ? <span className="text-muted">No workloads match &quot;{search}&quot;.</span> : <NoInfrastructureHint subject="workloads" />}
        storageKey="infrastructure-workloads"
        rowClassName={(w) => (w.id === highlightWorkloadId ? "bg-accent/10 pyxie-jump-target" : "")}
        columns={columns}
        singleExpand
        defaultsVersion={1}
        renderDetail={(w) =>
          w.is_missing ? null : (
            <WorkloadRowDetail
              workload={{
                ...w,
                sensitivity: sensitivity[w.id] ?? w.sensitivity,
                downtime_tolerance: downtimeTolerance[w.id] ?? w.downtime_tolerance,
                storage_preference: storagePref[w.id] ?? null,
                preferred_node_id: preferredNode[w.id] ?? null,
                placement_notes: notes[w.id] ?? w.placement_notes,
              }}
              findings={(findingsByWorkload.get(w.id) || []).map((f) => ({ id: f.id, severity: f.severity, title: f.title }))}
              clusterNodes={nodesByCluster.get(w.cluster_id) || []}
              profile={{
                values: {
                  sensitivity: sensitivity[w.id] ?? w.sensitivity,
                  downtime_tolerance: downtimeTolerance[w.id] ?? w.downtime_tolerance,
                  storage_preference: storagePref[w.id] ?? null,
                  preferred_node_id: preferredNode[w.id] ?? null,
                },
                onChange: (patch) => {
                  if (patch.sensitivity !== undefined) setSensitivity((m) => ({ ...m, [w.id]: patch.sensitivity as string }));
                  if (patch.downtime_tolerance !== undefined) setDowntimeTolerance((m) => ({ ...m, [w.id]: patch.downtime_tolerance as string }));
                  if (patch.storage_preference !== undefined) setStoragePref((m) => ({ ...m, [w.id]: patch.storage_preference as string | null }));
                  if (patch.preferred_node_id !== undefined) setPreferredNode((m) => ({ ...m, [w.id]: patch.preferred_node_id as string | null }));
                },
              }}
            />
          )
        }
      />
      {sortedSearchedWorkloads.length > 0 && (
        <div className="flex items-center justify-between mt-2 text-xs text-muted">
          <span>
            {sortedSearchedWorkloads.length} workload{sortedSearchedWorkloads.length === 1 ? "" : "s"}
          </span>
          {totalPages > 1 && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={currentPage <= 1}
                className="px-2 py-1 rounded border border-border hover:bg-surface2 disabled:opacity-40"
              >
                ← Prev
              </button>
              <span>
                Page {currentPage} of {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage >= totalPages}
                className="px-2 py-1 rounded border border-border hover:bg-surface2 disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
