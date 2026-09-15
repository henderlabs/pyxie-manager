import { apiFetch } from "@/lib/api";
import type { BackupJob, Provider, ProtectionResultRow, Site } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import ActionButton from "@/components/ActionButton";
import PbsTargetForm from "@/components/PbsTargetForm";
import ProtectionResultsTable from "@/components/tables/ProtectionResultsTable";
import BackupJobMembership from "@/components/BackupJobMembership";
import { ShieldIcon } from "@/components/Icons";

type ProtectionTarget = {
  id: string;
  provider_type: string;
  name: string;
  hostname: string;
  connection_health: string;
};

export default async function ProtectionPage() {
  const [providers, targets, results, sites, backupJobs] = await Promise.all([
    apiFetch<Provider[]>("/api/providers"),
    apiFetch<ProtectionTarget[]>("/api/protection/targets"),
    apiFetch<ProtectionResultRow[]>("/api/protection/results"),
    apiFetch<Site[]>("/api/sites"),
    apiFetch<BackupJob[]>("/api/protection/backup-jobs"),
  ]);

  const protectionProviders = providers.filter((p) => p.category_id === "protection");
  const defaultSite = sites[0];

  const protectedCount = results.filter((r) => r.protected === "true").length;
  const unprotectedCount = results.filter((r) => r.protected === "false").length;
  const unknownCount = results.filter((r) => r.protected === "unknown").length;

  return (
    <div>
      <PageHeader title="Protection" subtitle="Backup/DR posture per workload -- UNKNOWN is not SAFE" icon={<ShieldIcon className="w-5 h-5" />} />

      <Card className="mb-4">
        <CardTitle>Provider Implementation / Validation Matrix</CardTitle>
        <div className="divide-y divide-border text-sm">
          {protectionProviders.map((p) => (
            <div key={p.id} className="flex items-center justify-between py-2">
              <span className="text-text">{p.name}</span>
              <div className="flex gap-3 items-center text-xs">
                <span className="text-muted">
                  Implementation: <span className="text-text">{p.implementation_status.replace(/_/g, " ")}</span>
                </span>
                <span className="text-muted">
                  Live Validation: <span className="text-text">{p.live_validation_status.replace(/_/g, " ")}</span>
                </span>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card className="mb-4">
        <CardTitle>PBS Targets</CardTitle>
        <div className="space-y-3">
          {targets.map((t) => (
            <div key={t.id} className="flex items-center justify-between bg-surface2 rounded p-3">
              <div>
                <div className="text-sm text-text">{t.name}</div>
                <div className="text-xs text-muted">{t.hostname}</div>
              </div>
              <div className="flex items-center gap-3">
                <StatusBadge status={t.connection_health} />
                <ActionButton href={`/api/protection/targets/${t.id}/sync`} label="Sync Now" variant="primary" />
              </div>
            </div>
          ))}
          {defaultSite && <PbsTargetForm siteId={defaultSite.id} />}
        </div>
      </Card>

      <Card className="mb-4">
        <CardTitle>Protection Summary</CardTitle>
        <div className="grid grid-cols-3 gap-4 text-center">
          <div>
            <div className="text-xs text-muted flex items-center justify-center gap-1 mb-1">
              <ShieldIcon className="w-4 h-4 text-good/70" />
              Protected
            </div>
            <div className="text-2xl font-semibold text-good">{protectedCount}</div>
          </div>
          <div>
            <div className="text-xs text-muted flex items-center justify-center gap-1 mb-1">
              <ShieldIcon className="w-4 h-4 text-bad/70" />
              Unprotected
            </div>
            <div className="text-2xl font-semibold text-bad">{unprotectedCount}</div>
          </div>
          <div>
            <div className="text-xs text-muted flex items-center justify-center gap-1 mb-1">
              <ShieldIcon className="w-4 h-4 text-muted/70" />
              Unknown
            </div>
            <div className="text-2xl font-semibold text-muted">{unknownCount}</div>
          </div>
        </div>
      </Card>

      <Card className="mb-4">
        <CardTitle>PBS Backup Job Membership</CardTitle>
        <div className="text-xs text-muted mb-3">
          Add/remove a VM from a PBS-backed vzdump job, or switch it to PVE&apos;s native all-guests mode. Scoped to
          PBS-targeted jobs only -- a job backed by any other storage type never appears here.
        </div>
        <BackupJobMembership jobs={backupJobs} />
      </Card>

      <Card>
        <CardTitle>Per-Workload Protection Status</CardTitle>
        <ProtectionResultsTable results={results} />
      </Card>
    </div>
  );
}
