"use client";

import Link from "next/link";
import type { PveTask } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import { LogDetailGrid } from "@/components/LogDetail";
import TaskLogViewer from "@/components/TaskLogViewer";

export default function PveTasksTable({
  tasks,
  nodeNameById = {},
  clusterNameById = {},
  workloadIdByVmidNode = {},
}: {
  tasks: PveTask[];
  nodeNameById?: Record<string, string>;
  clusterNameById?: Record<string, string>;
  /** Keyed "{node_id}:{vmid}" -> workload id, so the VM column can link to
   * the Workloads page the same way every other VM reference in the app
   * does, not just show plain text. */
  workloadIdByVmidNode?: Record<string, string>;
}) {
  return (
    <Table
      rows={tasks}
      emptyMessage="No PVE tasks recorded yet."
      storageKey="operations-pve-tasks"
      columns={[
        {
          header: "Type",
          render: (t) => t.task_type || t.upid.split(":")[1] || "—",
          sortValue: (t) => t.task_type || t.upid,
        },
        { header: "Status", render: (t) => <StatusBadge status={t.status || "unknown"} />, sortValue: (t) => t.status },
        {
          header: "VM",
          tooltip: "The VM/CT this task acted on, resolved from PVE's own task record -- blank for a node-level task (host reboot, storage rescan, etc).",
          render: (t) => {
            if (t.vmid == null) return "—";
            const wid = t.node_id ? workloadIdByVmidNode[`${t.node_id}:${t.vmid}`] : undefined;
            const label = t.workload_name ? `${t.workload_name} (vmid ${t.vmid})` : `vmid ${t.vmid}`;
            return wid ? (
              <Link href={`/infrastructure/workloads?workload=${wid}`} className="text-accent hover:underline">
                {label}
              </Link>
            ) : (
              label
            );
          },
          sortValue: (t) => t.workload_name || t.vmid || "",
        },
        {
          header: "Node",
          render: (t) => (t.node_id && nodeNameById[t.node_id]) || "—",
          sortValue: (t) => (t.node_id && nodeNameById[t.node_id]) || "",
        },
        { header: "User", render: (t) => t.user || "—", sortValue: (t) => t.user, optional: true },
        {
          header: "Started",
          render: (t) => (t.started_at ? new Date(t.started_at).toLocaleString() : "—"),
          sortValue: (t) => t.started_at,
        },
        {
          header: "Ended",
          render: (t) => (t.ended_at ? new Date(t.ended_at).toLocaleString() : "—"),
          sortValue: (t) => t.ended_at,
          optional: true,
        },
      ]}
      renderDetail={(t) => {
        // UPID:node:pid:pstart:starttime:type:id:user: -- kept here as a
        // raw reference field even though vmid/workload_name are now
        // resolved server-side and shown in their own VM column above.
        return (
          <div>
            <LogDetailGrid
              fields={[
                ["UPID", t.upid],
                ["Exit status", t.exit_status],
                ["VMID", t.vmid],
                ["Workload", t.workload_name],
                ["Cluster", (t.cluster_id && clusterNameById[t.cluster_id]) || t.cluster_id],
                ["Node", (t.node_id && nodeNameById[t.node_id]) || t.node_id],
              ]}
            />
            <TaskLogViewer taskId={t.id} />
          </div>
        );
      }}
    />
  );
}
