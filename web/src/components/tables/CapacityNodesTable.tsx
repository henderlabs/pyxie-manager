"use client";

import type { NodeCapacity } from "@/lib/api";
import { Table } from "@/components/Table";
import { formatBytes, formatPct } from "@/lib/format";
import { Meter } from "@/components/Gauges";

export default function CapacityNodesTable({ nodes }: { nodes: NodeCapacity[] }) {
  return (
    <Table
      rows={nodes.map((n) => ({ ...n, id: n.node_id }))}
      emptyMessage="No nodes in this cluster."
      storageKey="operations-capacity-nodes"
      columns={[
        {
          header: "Node",
          render: (n) => (
            <div className="flex items-center gap-2">
              <span>{n.name}</span>
              {n.maintenance_mode && (
                <span
                  className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-warn/20 text-warn uppercase tracking-wide"
                  title="In maintenance mode"
                >
                  Maint
                </span>
              )}
            </div>
          ),
          sortValue: (n) => n.name,
        },
        { header: "Allocated vCPU", render: (n) => n.allocated_vcpu, sortValue: (n) => n.allocated_vcpu },
        {
          header: "Allocated RAM",
          render: (n) => formatBytes(n.allocated_memory_bytes),
          sortValue: (n) => n.allocated_memory_bytes,
        },
        {
          header: "Observed CPU (P95)",
          render: (n) => (
            <div className="flex items-center gap-2">
              <Meter value={n.observed_cpu_p95_pct} width={56} />
              <span className="text-muted text-xs whitespace-nowrap">avg {formatPct(n.observed_cpu_pct)}</span>
            </div>
          ),
          sortValue: (n) => n.observed_cpu_p95_pct,
        },
        {
          header: "Observed RAM (P95)",
          render: (n) => (
            <div className="flex items-center gap-2">
              <Meter value={n.observed_mem_p95_pct} width={56} />
              <span className="text-muted text-xs whitespace-nowrap">avg {formatPct(n.observed_mem_pct)}</span>
            </div>
          ),
          sortValue: (n) => n.observed_mem_p95_pct,
          optional: true,
        },
        {
          header: "RAM Headroom",
          render: (n) => formatBytes(n.memory_headroom_bytes),
          sortValue: (n) => n.memory_headroom_bytes,
          optional: true,
        },
        { header: "Workloads", render: (n) => n.workload_count, sortValue: (n) => n.workload_count, optional: true },
      ]}
    />
  );
}
