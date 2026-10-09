import { apiFetch } from "@/lib/api";
import type { Cluster, Node, PolicyRow, Site, StorageItem, Workload } from "@/lib/api";
import { Card, CardTitle, EmptyState, NoInfrastructureHint, PageHeader, StatTile } from "@/components/Card";
import NodesTable, { type TierSuggestion } from "@/components/tables/NodesTable";
import ClusterStoragePreference from "@/components/ClusterStoragePreference";
import StatusBadge from "@/components/StatusBadge";
import { MapPinIcon, ClusterIcon } from "@/components/Icons";
import { formatBytes } from "@/lib/format";

export default async function HostsClustersPage({
  searchParams,
}: {
  searchParams: { site?: string; cluster?: string };
}) {
  if (searchParams.site) {
    return <SiteOverview siteId={searchParams.site} />;
  }
  return <ClusterOverview clusterId={searchParams.cluster} />;
}

async function SiteOverview({ siteId }: { siteId: string }) {
  const [sites, clusters, nodes, workloads] = await Promise.all([
    apiFetch<Site[]>("/api/sites"),
    apiFetch<Cluster[]>("/api/clusters"),
    apiFetch<Node[]>("/api/nodes"),
    apiFetch<Workload[]>("/api/workloads"),
  ]);
  const site = sites.find((s) => s.id === siteId);
  if (!site) return <EmptyState message="Site not found." />;

  const siteClusters = clusters.filter((c) => c.site_id === site.id);
  const clusterIds = new Set(siteClusters.map((c) => c.id));
  const siteNodes = nodes.filter((n) => clusterIds.has(n.cluster_id));
  const siteWorkloads = workloads.filter((w) => clusterIds.has(w.cluster_id));
  const onlineCount = siteNodes.filter((n) => n.status === "online").length;
  const totalUpdates = siteNodes.reduce((sum, n) => sum + (n.pending_updates ?? 0), 0);
  // Excludes is_missing -- a removed guest's old allocation isn't real
  // capacity in use anymore.
  const presentWorkloads = siteWorkloads.filter((w) => !w.is_missing);
  const vmCount = presentWorkloads.filter((w) => w.type === "vm").length;
  const ctCount = presentWorkloads.filter((w) => w.type === "lxc").length;
  const allocatedVcpu = presentWorkloads.reduce((sum, w) => sum + (w.cpu_cores ?? 0), 0);
  const allocatedMemBytes = presentWorkloads.reduce((sum, w) => sum + (w.memory_bytes ?? 0), 0);

  return (
    <div className="contents">
      <div className="min-w-0">
      <PageHeader
        title={site.name}
        subtitle={`Site · ${siteClusters.length} cluster${siteClusters.length === 1 ? "" : "s"} · ${siteNodes.length} node${siteNodes.length === 1 ? "" : "s"}`}
        icon={<MapPinIcon className="w-5 h-5" />}
      />
      <div className="grid grid-cols-4 gap-3 mb-5">
        <StatTile label="Clusters" value={siteClusters.length} />
        <StatTile label="Nodes online" value={`${onlineCount} / ${siteNodes.length}`} />
        <StatTile label="Pending updates" value={totalUpdates} />
        <StatTile label="VMs" value={vmCount} />
        <StatTile label="Containers" value={ctCount} />
        <StatTile label="Allocated vCPU" value={allocatedVcpu} />
        <StatTile label="Allocated RAM" value={formatBytes(allocatedMemBytes)} />
      </div>
      </div>
      <p className="col-span-2 text-xs text-muted">Click a cluster in the tree for its own detail and node table.</p>
    </div>
  );
}

async function ClusterOverview({ clusterId }: { clusterId?: string }) {
  const [clusters, nodes, workloads, tierSuggestions, nodePolicies, clusterPolicies, storage] = await Promise.all([
    apiFetch<Cluster[]>("/api/clusters"),
    apiFetch<Node[]>("/api/nodes"),
    apiFetch<Workload[]>("/api/workloads"),
    apiFetch<Record<string, TierSuggestion>>("/api/placement/node-tier-suggestions"),
    apiFetch<PolicyRow[]>("/api/policies?scope_type=node"),
    apiFetch<PolicyRow[]>("/api/policies?scope_type=cluster"),
    apiFetch<StorageItem[]>("/api/storage"),
  ]);

  const cluster = clusters.find((c) => c.id === (clusterId ?? clusters[0]?.id));
  if (!cluster) return <EmptyState message={<NoInfrastructureHint subject="clusters" />} />;

  const clusterNodes = nodes.filter((n) => n.cluster_id === cluster.id);
  const clusterWorkloads = workloads.filter((w) => w.cluster_id === cluster.id);
  const onlineCount = clusterNodes.filter((n) => n.status === "online").length;
  const withStats = clusterNodes.filter((n) => n.cpu_usage_pct != null);
  const avgCpu = withStats.length ? withStats.reduce((s, n) => s + (n.cpu_usage_pct ?? 0), 0) / withStats.length : null;
  const avgRam = withStats.length ? withStats.reduce((s, n) => s + (n.mem_usage_pct ?? 0), 0) / withStats.length : null;
  const pveVersions = Array.from(new Set(clusterNodes.map((n) => n.pve_version).filter(Boolean)));
  // Excludes is_missing -- a removed guest's old allocation isn't real
  // capacity in use anymore.
  const presentWorkloads = clusterWorkloads.filter((w) => !w.is_missing);
  const vmCount = presentWorkloads.filter((w) => w.type === "vm").length;
  const ctCount = presentWorkloads.filter((w) => w.type === "lxc").length;
  const allocatedVcpu = presentWorkloads.reduce((sum, w) => sum + (w.cpu_cores ?? 0), 0);
  const allocatedMemBytes = presentWorkloads.reduce((sum, w) => sum + (w.memory_bytes ?? 0), 0);

  const defaultStorageByNode = Object.fromEntries(
    nodePolicies.filter((p) => p.key === "placement.default_storage_id").map((p) => [p.scope_id, (p.value as string | null) || null])
  );
  const storagePref =
    clusterPolicies.find((p) => p.key === "placement.storage_preference" && p.scope_id === cluster.id)?.value ?? null;

  return (
    <div className="contents">
      <div className="min-w-0">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            {cluster.name}
            <StatusBadge status={cluster.is_missing ? "unknown" : cluster.quorate === false ? "critical" : "healthy"} />
          </span>
        }
        subtitle={
          pveVersions.length > 1
            ? `Cluster · Mixed PVE ${pveVersions.join(" / ")}`
            : pveVersions[0]
            ? `Cluster · PVE ${pveVersions[0]}`
            : "Cluster · PVE version unknown"
        }
        icon={<ClusterIcon className="w-5 h-5" />}
      />

      <div className="grid grid-cols-4 gap-3 mb-5">
        <StatTile label="Nodes online" value={`${onlineCount} / ${clusterNodes.length}`} />
        <StatTile label="Avg CPU" value={avgCpu != null ? `${avgCpu.toFixed(1)}%` : "—"} />
        <StatTile label="Avg RAM" value={avgRam != null ? `${avgRam.toFixed(1)}%` : "—"} />
        <StatTile label="VMs" value={vmCount} />
        <StatTile label="Containers" value={ctCount} />
        <StatTile label="Allocated vCPU" value={allocatedVcpu} />
        <StatTile label="Allocated RAM" value={formatBytes(allocatedMemBytes)} />
      </div>

      <Card className="mb-5">
        <CardTitle>Default storage preference</CardTitle>
        <p className="text-[11px] text-muted mb-2 normal-case">
          Fallback used when a VM has no storage preference of its own (Workloads page) -- a per-VM setting always wins over this.
        </p>
        <ClusterStoragePreference clusterId={cluster.id} initialValue={storagePref as string | null} />
      </Card>

      </div>

      <div className="col-span-2 min-w-0">
      <div className="text-[11px] uppercase tracking-wider text-muted font-semibold mb-2">Nodes in this cluster</div>
      <NodesTable
        nodes={clusterNodes}
        clusterId={cluster.id}
        tierSuggestions={tierSuggestions}
        storage={storage}
        defaultStorageByNode={defaultStorageByNode}
      />
      </div>
    </div>
  );
}
