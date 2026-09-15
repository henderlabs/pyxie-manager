"use client";

import type { ProtectionResultRow } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";

export default function ProtectionResultsTable({ results }: { results: ProtectionResultRow[] }) {
  return (
    <Table
      rows={results.map((r) => ({ ...r, id: r.workload_id }))}
      emptyMessage="No protection data yet -- configure a PBS target above and run a sync."
      storageKey="operations-protection-results"
      columns={[
        { header: "VMID", render: (r) => r.vmid ?? "—", sortValue: (r) => r.vmid },
        { header: "Name", render: (r) => r.name || "—", sortValue: (r) => r.name },
        { header: "Protected", render: (r) => <StatusBadge status={r.protected} />, sortValue: (r) => r.protected },
        {
          header: "Last Backup",
          render: (r) => (r.last_successful_job_at ? new Date(r.last_successful_job_at).toLocaleString() : "—"),
          sortValue: (r) => r.last_successful_job_at,
        },
        { header: "SLA", render: (r) => <StatusBadge status={r.sla_compliant} />, sortValue: (r) => r.sla_compliant },
        {
          header: "Confidence",
          render: (r) => (r.confidence ? <StatusBadge status={r.confidence} /> : "—"),
          sortValue: (r) => r.confidence,
          optional: true,
        },
      ]}
    />
  );
}
