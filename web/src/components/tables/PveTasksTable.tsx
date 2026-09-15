"use client";

import type { PveTask } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import { LogDetailGrid } from "@/components/LogDetail";

export default function PveTasksTable({
  tasks,
  nodeNameById = {},
  clusterNameById = {},
}: {
  tasks: PveTask[];
  nodeNameById?: Record<string, string>;
  clusterNameById?: Record<string, string>;
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
        // UPID:node:pid:pstart:starttime:type:id:user: -- `id` (the 7th
        // segment) is the vmid/CT id a VM-scoped task acted on, empty for
        // a node-level task. Not stored as its own column (see PveTask),
        // and otherwise invisible anywhere in the UI.
        const parts = t.upid.split(":");
        const targetId = parts[6];
        return (
          <LogDetailGrid
            fields={[
              ["UPID", t.upid],
              ["Target VMID", targetId],
              ["Cluster", (t.cluster_id && clusterNameById[t.cluster_id]) || t.cluster_id],
              ["Node", (t.node_id && nodeNameById[t.node_id]) || t.node_id],
            ]}
          />
        );
      }}
    />
  );
}
