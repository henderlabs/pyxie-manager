import { apiFetch } from "@/lib/api";
import type { ClusterCapacity } from "@/lib/api";
import { Card, CardTitle, EmptyState, PageHeader, StatTile } from "@/components/Card";
import CapacityNodesTable from "@/components/tables/CapacityNodesTable";
import { formatBytes } from "@/lib/format";
import { Meter } from "@/components/Gauges";
import { MemoryIcon, ServerIcon, GaugeIcon } from "@/components/Icons";

export default async function CapacityPage() {
  const clusters = await apiFetch<ClusterCapacity[]>("/api/capacity");

  return (
    <div>
      <PageHeader title="Capacity" subtitle="Allocated capacity vs. observed consumption" icon={<GaugeIcon className="w-5 h-5" />} />
      {clusters.length === 0 && (
        <Card>
          <EmptyState message="No clusters discovered yet." />
        </Card>
      )}
      {clusters.map((c) => (
        <Card key={c.cluster_id} className="mb-4">
          <CardTitle>{c.name}</CardTitle>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
            <StatTile
              label="Memory Allocated"
              icon={<MemoryIcon />}
              value={<Meter value={c.memory_allocation_pct} width={80} />}
              sub={`${formatBytes(c.total_allocated_memory_bytes)} of ${formatBytes(c.total_memory_bytes)}`}
            />
            <StatTile label="Busiest Node" value={c.busiest_node || "—"} icon={<ServerIcon />} />
            <StatTile label="Most Constrained" value={c.most_constrained_resource || "—"} icon={<GaugeIcon />} />
            <StatTile label="Nodes" value={c.nodes.length} icon={<ServerIcon />} />
          </div>

          <div className="mb-4">
            <CapacityNodesTable nodes={c.nodes} />
          </div>

          <CardTitle>Workloads contributing most CPU pressure</CardTitle>
          <div className="text-sm divide-y divide-border">
            {c.workloads_contributing_most_pressure.length === 0 && <div className="text-muted py-2">Not enough observed data yet.</div>}
            {c.workloads_contributing_most_pressure.map((w) => (
              <div key={w.vmid} className="flex justify-between py-1.5">
                <span className="text-text">
                  {w.name || "unnamed"} <span className="text-muted">(vmid {w.vmid})</span>
                </span>
                <span className="text-muted">score {w.cpu_pressure_score}</span>
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}
