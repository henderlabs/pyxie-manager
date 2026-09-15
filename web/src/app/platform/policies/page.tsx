import { apiFetch } from "@/lib/api";
import type { Cluster, Node, PolicyRow, StorageItem } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import PolicyEditor from "@/components/PolicyEditor";
import { SlidersIcon } from "@/components/Icons";
import { POLICY_META } from "@/lib/policyMeta";

export default async function PoliciesPage() {
  const [policies, nodes, clusters, storage] = await Promise.all([
    apiFetch<PolicyRow[]>("/api/policies"),
    apiFetch<Node[]>("/api/nodes"),
    apiFetch<Cluster[]>("/api/clusters"),
    apiFetch<StorageItem[]>("/api/storage"),
  ]);
  // Raw scope_id is a UUID -- resolve it to the node/cluster name the rest
  // of the app already shows, since a policy row is otherwise unreadable
  // without cross-referencing another page. storageNameById does the same
  // for a policy VALUE that happens to be a storage reference
  // (placement.default_storage_id) -- a policy scoped to one host's
  // storage was still showing the raw storage UUID as its value, not
  // just its scope.
  const nameByScopeId: Record<string, string> = Object.fromEntries([
    ...nodes.map((n) => [n.id, n.name]),
    ...clusters.map((c) => [c.id, c.name]),
  ]);
  const storageNameById: Record<string, string> = Object.fromEntries(storage.map((s) => [s.id, s.name]));

  return (
    <div>
      <PageHeader
        title="Policies"
        subtitle="Advanced -- raw policy keys and values. Everyday settings for a node/cluster/VM live on the page that uses them (Hosts & Clusters, Workloads); narrower scope overrides broader scope here."
        icon={<SlidersIcon className="w-5 h-5" />}
      />
      <Card>
        <CardTitle>All Policies</CardTitle>
        <div className="divide-y divide-border">
          {policies.map((p) => {
            const meta = POLICY_META[p.key];
            return (
              <div key={p.id} className="py-3">
                <div className="mb-2">
                  <div className="text-sm text-text font-medium">{meta?.label || p.key}</div>
                  {meta?.description && <div className="text-xs text-muted mt-0.5">{meta.description}</div>}
                  <div className="text-[11px] text-muted/70 font-mono mt-1">
                    {p.key} · {p.scope_type}
                    {p.scope_id ? ` · ${nameByScopeId[p.scope_id] ?? p.scope_id}` : " · default"}
                  </div>
                </div>
                <PolicyEditor policy={p} storageNameById={storageNameById} />
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}
