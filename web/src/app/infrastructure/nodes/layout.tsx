import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import type { Cluster, Node, Site } from "@/lib/api";
import { PageHeader } from "@/components/Card";
import HostsClustersTree from "@/components/HostsClustersTree";
import { ServerIcon } from "@/components/Icons";

// Persistent vSphere-style inventory tree, shared across the whole
// /infrastructure/nodes route (this page, and every /infrastructure/nodes/[id]
// node-detail page) via a Next.js layout -- the tree never re-fetches or
// re-mounts as you click between site/cluster/node, only the detail panel
// on the right changes -- Sites/Clusters/Nodes combined into one page,
// browsed like vSphere's own Hosts and Clusters view.
export default async function HostsClustersLayout({ children }: { children: React.ReactNode }) {
  const [sites, clusters, nodes] = await Promise.all([
    apiFetch<Site[]>("/api/sites"),
    apiFetch<Cluster[]>("/api/clusters"),
    apiFetch<Node[]>("/api/nodes"),
  ]);

  return (
    <div>
      <PageHeader
        title="Hosts & Clusters"
        subtitle="Site → cluster → host -- click any level in the tree"
        icon={<ServerIcon className="w-5 h-5" />}
      />
      <div className="grid gap-4 items-start" style={{ gridTemplateColumns: "260px 1fr" }}>
        <div className="border border-border rounded-lg bg-surface2/40 overflow-hidden">
          <Suspense fallback={null}>
            <HostsClustersTree sites={sites} clusters={clusters} nodes={nodes} />
          </Suspense>
        </div>
        {/* `contents` lets a page place its own pieces in this grid: the cluster page puts its summary beside the tree and its
            nodes table on the next row across the full width (col-span-2). Other pages keep one block beside the tree. */}
        <div className="contents">{children}</div>
      </div>
    </div>
  );
}
