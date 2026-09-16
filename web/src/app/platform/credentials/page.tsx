import { apiFetch } from "@/lib/api";
import type { Cluster, Credential, HostMaintenanceCredentialRecord, Node, PveTarget } from "@/lib/api";
import { Card, CardTitle, EmptyState, PageHeader } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import HostMaintenanceSetup from "@/components/HostMaintenanceSetup";
import AddCredentialForm from "@/components/AddCredentialForm";
import EditCredentialForm from "@/components/EditCredentialForm";
import { KeyIcon } from "@/components/Icons";
import Link from "next/link";

export default async function CredentialsPage() {
  const [targets, clusters, nodes] = await Promise.all([
    apiFetch<PveTarget[]>("/api/pve-targets"),
    apiFetch<Cluster[]>("/api/clusters"),
    apiFetch<Node[]>("/api/nodes"),
  ]);
  const credentialsByTarget = await Promise.all(
    targets.map((t) => apiFetch<Credential[]>(`/api/pve-targets/${t.id}/credentials`))
  );
  const hostMaintCredByTarget = await Promise.all(
    targets.map((t) =>
      apiFetch<HostMaintenanceCredentialRecord | null>(`/api/pve-targets/${t.id}/host-maintenance-credential`).catch(() => null)
    )
  );

  const clusterIdsByTarget = (targetId: string) => clusters.filter((c) => c.pve_target_id === targetId).map((c) => c.id);
  const nodesForTarget = (targetId: string) => {
    const clusterIds = new Set(clusterIdsByTarget(targetId));
    return nodes.filter((n) => clusterIds.has(n.cluster_id));
  };

  return (
    <div>
      <PageHeader title="Credentials" subtitle="One API token per access level (credential purpose), per PVE target — secret material is never redisplayed" icon={<KeyIcon className="w-5 h-5" />} />
      {targets.length === 0 && (
        <Card>
          <EmptyState
            message={
              <>
                No PVE targets configured yet.{" "}
                <Link href="/platform/providers" className="text-accent hover:underline">
                  Add one under Integrations
                </Link>
                .
              </>
            }
          />
        </Card>
      )}
      {targets.map((t, i) => (
        <Card key={t.id} className="mb-4">
          <CardTitle>{t.name}</CardTitle>
          <div className="divide-y divide-border mb-4">
            {credentialsByTarget[i].map((c) => (
              <div key={c.id} className="py-2 text-sm">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-text font-medium">{c.slot_name}</div>
                    <div className="text-xs text-muted">
                      {c.token_user}!{c.token_id}
                    </div>
                  </div>
                  <div className="text-xs text-muted font-mono">{c.masked_secret}</div>
                  <StatusBadge status={c.status} />
                  <div className="text-xs text-muted">
                    {c.last_validated_at ? `validated ${new Date(c.last_validated_at).toLocaleString()}` : "never validated"}
                  </div>
                </div>
                <EditCredentialForm targetId={t.id} credential={c} />
              </div>
            ))}
          </div>

          <div className="mb-4">
            <AddCredentialForm targetId={t.id} existingSlots={credentialsByTarget[i].map((c) => c.slot_name)} />
          </div>

          <div className="border-t border-border pt-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-proxmox mb-2">
              Host Maintenance — Connect a Host
            </div>
            <HostMaintenanceSetup targetId={t.id} nodes={nodesForTarget(t.id)} initialCredential={hostMaintCredByTarget[i]} />
          </div>
        </Card>
      ))}
    </div>
  );
}
