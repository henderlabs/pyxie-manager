"use client";

import Link from "next/link";
import type { Workload } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import { formatBytes } from "@/lib/format";
import { CpuIcon, MemoryIcon } from "@/components/Icons";

export default function NodeWorkloadsTable({ workloads }: { workloads: Workload[] }) {
  return (
    <Table
      rows={workloads}
      emptyMessage="No workloads on this node."
      storageKey="node-detail-workloads"
      columns={[
        { header: "VMID", render: (w) => w.vmid, sortValue: (w) => w.vmid },
        {
          header: "Name",
          render: (w) =>
            w.name ? (
              <Link href={`/infrastructure/workloads/${w.id}`} className="text-accent hover:underline" title="Open this workload">
                {w.name}
              </Link>
            ) : (
              "—"
            ),
          sortValue: (w) => w.name,
        },
        { header: "Type", render: (w) => w.type.toUpperCase(), sortValue: (w) => w.type },
        { header: "Status", render: (w) => <StatusBadge status={w.status} />, sortValue: (w) => w.status },
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
      ]}
    />
  );
}
