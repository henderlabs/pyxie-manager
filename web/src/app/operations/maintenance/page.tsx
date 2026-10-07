import { apiFetch } from "@/lib/api";
import type { Node, Operation, PolicyRow, Recommendation, StorageItem, Workload } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import MaintenanceWorkspace from "@/components/MaintenanceWorkspace";
import NodeOperationsLog from "@/components/NodeOperationsLog";
import RecommendationsList from "@/components/RecommendationsList";
import { WrenchIcon } from "@/components/Icons";

const W2_W6_TYPES = new Set([
  "node.evacuate", "workload.shutdown", "workload.start", "workload.force_stop", "workload.reboot",
  "host.update", "host.reboot", "maintenance.run",
]);

export default async function MaintenancePage({
  searchParams,
}: {
  searchParams: { node?: string; action?: string; workload?: string };
}) {
  // Deliberately only cheap DB reads here -- host-maintenance-status (the
  // SSH probe behind the Reboot Required badge/banner) used to be fetched
  // here too, blocking this whole page on 1-3+ seconds PER NODE. It's now
  // owned entirely by MaintenanceWorkspace (client-side, fetched once,
  // right after the page shell has already painted) -- see its own
  // comment for the measurement that motivated this.
  const [nodes, workloads, operations, workloadMetrics, workloadStorage, storage, recommendations, nodePolicies] =
    await Promise.all([
      apiFetch<Node[]>("/api/nodes"),
      apiFetch<Workload[]>("/api/workloads"),
      apiFetch<Operation[]>("/api/operations?limit=200"),
      apiFetch<Record<string, { cpu_pct?: number; mem_pct?: number; mem_source?: "guest" | "host" }>>("/api/workloads/latest-metrics"),
      apiFetch<Record<string, { name: string; scope: string | null }>>("/api/workloads/current-storage"),
      apiFetch<StorageItem[]>("/api/storage"),
      apiFetch<Recommendation[]>("/api/recommendations?status=any"),
      apiFetch<PolicyRow[]>("/api/policies?scope_type=node"),
    ]);

  const relevant = operations.filter((o) => W2_W6_TYPES.has(o.operation_type_id));

  const defaultStorageByNode = Object.fromEntries(
    nodePolicies
      .filter((p) => p.key === "placement.default_storage_id")
      .map((p) => [p.scope_id, (p.value as string | null) || null])
  );

  // Rightsizing lives on its own page now (2026-09-12) -- everything else
  // (updates, capacity, load-balancing) is a cluster/node-level concern,
  // i.e. a maintenance action, so it lives here instead.
  const maintenanceRecs = recommendations.filter((r) => r.category !== "rightsizing");

  return (
    <div>
      <PageHeader
        title="Maintenance"
        subtitle="Node evacuation, guest shutdown/start, host updates, host reboot, and full maintenance runs -- every action previews first; most only run after you approve, except Apply Updates, which auto-applies once its own check comes back clean."
        icon={<WrenchIcon className="w-5 h-5" />}
      />

      <MaintenanceWorkspace
        nodes={nodes}
        workloads={workloads}
        workloadMetrics={workloadMetrics}
        workloadStorage={workloadStorage}
        storage={storage}
        defaultStorageByNode={defaultStorageByNode}
        initialNodeId={searchParams.node}
        initialActionKey={searchParams.action}
        initialWorkloadId={searchParams.workload}
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        <Card>
          <CardTitle>Recommendations</CardTitle>
          <RecommendationsList
            recommendations={maintenanceRecs}
            emptyMessage="No open updates, capacity, or load-balancing recommendations right now."
          />
        </Card>
        <NodeOperationsLog initialOperations={relevant} />
      </div>
    </div>
  );
}
