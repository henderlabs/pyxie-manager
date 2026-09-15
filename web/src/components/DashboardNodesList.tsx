"use client";

import { useEffect, useState } from "react";
import type { Node } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import { Meter } from "@/components/Gauges";
import { PackageIcon } from "@/components/Icons";

type NodeDetail = {
  id: string;
  name: string;
  status: string;
  cpu_usage_pct: number | null;
  mem_usage_pct: number | null;
  pending_updates: number | null;
  maintenance_mode: boolean;
};

// CPU/RAM polled every 5s -- cheap DB read, same pattern as the
// Maintenance/Nodes pages. A real gap found live: the Dashboard's node
// meters were a one-time server snapshot with no refresh of their own,
// unlike Maintenance's -- inconsistent "live" behavior across pages was exactly
// the complaint. Sortable via the shared Table component, same as every
// other page showing live usage now.
export default function DashboardNodesList({ initialNodes }: { initialNodes: NodeDetail[] }) {
  const [nodes, setNodes] = useState<NodeDetail[]>(initialNodes);

  useEffect(() => {
    function refresh() {
      fetch("/api/nodes")
        .then((r) => (r.ok ? r.json() : null))
        .then((data: Node[] | null) => data && setNodes(data))
        .catch(() => {});
    }
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <Table
      rows={nodes}
      emptyMessage="No nodes discovered yet. Configure a PVE target under Platform → Providers."
      storageKey="dashboard-nodes"
      columns={[
        {
          header: "Name",
          render: (n) => (
            <span className="flex items-center gap-1.5">
              {n.name}
              {n.maintenance_mode && (
                <span
                  className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-warn/20 text-warn uppercase tracking-wide"
                  title="In maintenance mode"
                >
                  Maint
                </span>
              )}
            </span>
          ),
          sortValue: (n) => n.name,
        },
        { header: "Status", render: (n) => <StatusBadge status={n.status} />, sortValue: (n) => n.status },
        { header: "CPU", render: (n) => <Meter value={n.cpu_usage_pct} width={64} />, sortValue: (n) => n.cpu_usage_pct },
        { header: "RAM", render: (n) => <Meter value={n.mem_usage_pct} width={64} />, sortValue: (n) => n.mem_usage_pct },
        {
          header: "Updates",
          render: (n) =>
            n.pending_updates ? (
              <span className="inline-flex items-center gap-1">
                <PackageIcon className="w-4 h-4" />
                {n.pending_updates}
              </span>
            ) : (
              "up to date"
            ),
          sortValue: (n) => n.pending_updates ?? 0,
        },
      ]}
    />
  );
}
