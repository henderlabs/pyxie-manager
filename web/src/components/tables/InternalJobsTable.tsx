"use client";

import type { InternalJobRunRow } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import { LogDetailGrid, prettyJson } from "@/components/LogDetail";

export default function InternalJobsTable({ jobs }: { jobs: InternalJobRunRow[] }) {
  return (
    <Table
      rows={jobs}
      emptyMessage="No internal jobs recorded yet."
      storageKey="operations-internal-jobs"
      columns={[
        { header: "Job", render: (j) => j.job_name.replace(/_/g, " "), sortValue: (j) => j.job_name },
        { header: "Status", render: (j) => <StatusBadge status={j.status} />, sortValue: (j) => j.status },
        { header: "Started", render: (j) => new Date(j.started_at).toLocaleString(), sortValue: (j) => j.started_at },
        {
          header: "Duration",
          render: (j) =>
            j.ended_at ? `${Math.round((new Date(j.ended_at).getTime() - new Date(j.started_at).getTime()) / 1000)}s` : "running",
          sortValue: (j) => (j.ended_at ? new Date(j.ended_at).getTime() - new Date(j.started_at).getTime() : null),
          optional: true,
        },
        {
          header: "Result / Error",
          // Truncated to keep the row scannable -- the untruncated text is
          // one click away in the detail panel below.
          render: (j) => j.error || (j.result_summary ? JSON.stringify(j.result_summary).slice(0, 80) : "—"),
          optional: true,
        },
      ]}
      renderDetail={(j) => (
        <LogDetailGrid
          fields={[
            ["Job ID", j.id],
            ["Ended", j.ended_at ? new Date(j.ended_at).toLocaleString() : null],
            ["Error", j.error],
            ["Result", prettyJson(j.result_summary)],
          ]}
        />
      )}
    />
  );
}
