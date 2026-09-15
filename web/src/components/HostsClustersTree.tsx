"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import type { Cluster, Node, Site } from "@/lib/api";
import { MapPinIcon, ClusterIcon, ServerIcon } from "@/components/Icons";

// vSphere-style "Hosts and Clusters" inventory tree. Sites/Clusters hold
// almost nothing on their own -- Nodes carries all the
// real weight -- so fold all three into one page navigated like this,
// instead of three separate list pages). Persistent across this whole
// route via layout.tsx; only the detail panel on the right changes as you
// click. Reads the URL itself (rather than being told what's selected by a
// server-rendered prop) since the node-detail route is a CHILD page this
// component has no other way to observe -- usePathname() sees the real
// current URL regardless of which page.tsx actually rendered.
export default function HostsClustersTree({
  sites,
  clusters,
  nodes,
}: {
  sites: Site[];
  clusters: Cluster[];
  nodes: Node[];
}) {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const nodeMatch = pathname.match(/^\/infrastructure\/nodes\/([^/]+)$/);
  const activeNodeId = nodeMatch ? nodeMatch[1] : null;
  const activeSiteId = !activeNodeId ? searchParams.get("site") : null;
  const activeClusterId = !activeNodeId && !activeSiteId ? searchParams.get("cluster") ?? clusters[0]?.id ?? null : null;

  return (
    <div className="text-sm py-2">
      {sites.map((site) => {
        const siteClusters = clusters.filter((c) => c.site_id === site.id);
        return (
          <div key={site.id}>
            <Link
              href={`/infrastructure/nodes?site=${site.id}`}
              className={`flex items-center gap-2 px-3 py-1.5 ${
                activeSiteId === site.id ? "bg-accent/15 text-text font-medium" : "text-muted hover:bg-surface2 hover:text-text"
              }`}
            >
              <MapPinIcon className="w-4 h-4 shrink-0" />
              <span className="truncate">{site.name}</span>
            </Link>
            {siteClusters.map((cluster) => {
              const clusterNodes = nodes.filter((n) => n.cluster_id === cluster.id);
              return (
                <div key={cluster.id}>
                  <Link
                    href={`/infrastructure/nodes?cluster=${cluster.id}`}
                    className={`flex items-center gap-2 px-3 py-1.5 pl-8 ${
                      activeClusterId === cluster.id ? "bg-accent/15 text-text font-medium" : "text-muted hover:bg-surface2 hover:text-text"
                    }`}
                  >
                    <ClusterIcon className="w-4 h-4 shrink-0" />
                    <span className="truncate">{cluster.name}</span>
                    {cluster.quorate === false && (
                      <span className="ml-auto text-[9px] uppercase font-bold px-1 rounded bg-bad/20 text-bad" title="Not quorate">
                        !
                      </span>
                    )}
                  </Link>
                  {clusterNodes.map((node) => (
                    <Link
                      key={node.id}
                      href={`/infrastructure/nodes/${node.id}`}
                      className={`flex items-center gap-2 px-3 py-1 pl-14 text-xs ${
                        activeNodeId === node.id ? "bg-accent/15 text-text font-medium" : "text-muted hover:bg-surface2 hover:text-text"
                      }`}
                    >
                      <ServerIcon className="w-3.5 h-3.5 shrink-0" />
                      <span className="truncate">{node.name}</span>
                      <span
                        className={`ml-auto w-1.5 h-1.5 rounded-full shrink-0 ${node.status === "online" ? "bg-good" : "bg-muted"}`}
                        title={node.status}
                      />
                    </Link>
                  ))}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
