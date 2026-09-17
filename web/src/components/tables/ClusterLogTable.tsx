"use client";

import type { ClusterLogEntry } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import { LogDetailGrid } from "@/components/LogDetail";

// Standard syslog priority scale PVE uses: 0=emerg .. 7=debug, lower is
// more severe. Mapped to the same "error"/"warning"/"info" vocabulary
// StatusBadge already colors consistently everywhere else in the app.
function severityFor(priority: number | null): string {
  if (priority == null) return "unknown";
  if (priority <= 3) return "error";
  if (priority === 4) return "warning";
  return "info";
}

export default function ClusterLogTable({ entries }: { entries: ClusterLogEntry[] }) {
  return (
    <Table
      rows={entries}
      emptyMessage="No cluster log entries recorded yet. PVE's own cluster log only captures infrequent system-level events (daemon restarts, quorum changes, hardware issues) -- unlike task history, it's normal for this to be sparse or empty most of the time."
      storageKey="platform-cluster-log"
      columns={[
        {
          header: "Time",
          render: (e) => (e.logged_at ? new Date(e.logged_at).toLocaleString() : "—"),
          sortValue: (e) => e.logged_at,
        },
        {
          header: "Severity",
          render: (e) => <StatusBadge status={severityFor(e.priority)} />,
          sortValue: (e) => e.priority,
        },
        {
          header: "Node",
          render: (e) => e.node || "—",
          sortValue: (e) => e.node,
        },
        { header: "Tag", render: (e) => e.tag || "—", sortValue: (e) => e.tag, optional: true },
        { header: "Message", render: (e) => e.message || "—", sortValue: (e) => e.message },
      ]}
      renderDetail={(e) => (
        <LogDetailGrid
          fields={[
            ["Entry ID", e.id],
            ["Node", e.node],
            ["Tag", e.tag],
            ["Priority", e.priority],
            ["Message", e.message],
          ]}
        />
      )}
    />
  );
}
