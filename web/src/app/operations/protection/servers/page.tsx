import Link from "next/link";
import { apiFetch } from "@/lib/api";
import type { BackupJob, Site } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import ActionButton from "@/components/ActionButton";
import PbsTargetForm from "@/components/PbsTargetForm";
import Retention from "@/components/Retention";
import { ShieldIcon } from "@/components/Icons";

type Copy = { store: string; offsite: boolean; backend: string; last: number | null; age_text: string; fresh: boolean };
type Row = { workload_id: string; vmid: number; name: string; type: string; status: "ok" | "partial" | "none"; copy_count: number; offsite: boolean; copies: Copy[]; problems: string[]; covered_by: string[] };
type Note = { level: string; text: string };
type Store = {
  name: string; backend: string; target: string; host: string; pbs_url: string; offsite: boolean; groups: number; snapshots: number; newest: number | null;
  prune: { id: string; schedule: string; retention: string; last_state: string | null }[];
  verify: { id: string; schedule: string; last_state: string | null }[];
  sync: { id: string; schedule: string; remote: string; last_state: string | null }[];
  notes: Note[];
};
type JobInfo = { cluster_id: string; cluster_name: string; job_id: string; storage: string; schedule: string; enabled: boolean; all_guests: boolean; member_count: number; scope_node: string | null; pve_url: string | null };
type Scorecard = { summary: { ok: number; partial: number; none: number; total: number }; rows: Row[]; stores: Store[]; jobs: JobInfo[]; errors: string[]; rules: { backup_max_age_h: number; offsite_max_age_h: number } };
type Server = { id: string; name: string; hostname: string; status: { reachable: boolean; version?: string; updates?: { count: number } | null } | null;
  credentials: Record<string, unknown>; ssh: { host_key_pinned: boolean } };

const when = (s: number | null) => (s ? new Date(s * 1000).toLocaleDateString() : "never");
const noteColor = (l: string) => (l === "bad" ? "text-bad" : l === "info" ? "text-muted" : "text-warn");
const statusText = { ok: "3-2-1 met", partial: "Partly protected", none: "Not protected" } as const;
const statusColor = { ok: "text-good", partial: "text-warn", none: "text-bad" } as const;

export default async function BackupServersPage() {
  const [card, servers, sites, backupJobs] = await Promise.all([
    apiFetch<Scorecard>("/api/protection/scorecard"),
    apiFetch<Server[]>("/api/pbs/servers"),
    apiFetch<Site[]>("/api/sites"),
    apiFetch<BackupJob[]>("/api/protection/backup-jobs"),
  ]);
  const defaultSite = sites[0];

  return (
    <div>
      <PageHeader title="Backup servers & storage" subtitle="Each datastore with how it is pruned, freed and verified, and the Proxmox jobs that fill it. A plan sets all of this for you: click a plan name to open its settings." icon={<ShieldIcon className="w-5 h-5" />} />

      {card.errors.map((e) => <div key={e} className="text-warn text-sm mb-2">{e}</div>)}

      <div className="grid grid-cols-1 gap-4 mb-4">

        <Card>
          <CardTitle>Backup servers</CardTitle>
          <div className="text-xs text-muted mb-2">Status and jobs on each server&apos;s page; package patching on <Link href="/operations/maintenance" className="text-accent hover:underline">Maintenance</Link>; accounts and access under <Link href="/platform/credentials" className="text-accent hover:underline">Settings, Credentials</Link>.</div>
        <div className="space-y-3">
          {servers.map((t) => (
            <div key={t.id} className="flex items-center justify-between bg-surface2 rounded p-3">
              <div>
                <Link href={`/operations/protection/servers/${t.id}`} className="text-sm text-text hover:underline">{t.name}</Link>
                <div className="text-xs text-muted">{t.hostname}{t.status?.reachable ? ` · PBS ${t.status.version}` : ""}
                  {t.status?.updates?.count ? <span className="text-warn"> · {t.status.updates.count} update(s) waiting: <Link href="/operations/maintenance" className="underline">patch in Maintenance</Link></span> : null}
                  {!t.credentials.inventory || !t.credentials.administrative || !t.ssh.host_key_pinned ? <span className="text-warn"> · setup incomplete</span> : null}</div>
              </div>
              <div className="flex items-center gap-3">
                <StatusBadge status={t.status ? (t.status.reachable ? "connected" : "unavailable") : "unknown"} />
                <Link href={`/operations/protection/servers/${t.id}`} className="text-xs text-accent hover:underline">Manage</Link>
                <ActionButton href={`/api/protection/targets/${t.id}/sync`} label="Sync Now" variant="primary" />
              </div>
            </div>
          ))}
          {defaultSite && <PbsTargetForm siteId={defaultSite.id} />}
        </div>
        </Card>
      </div>

      <Card className="mb-4">
        <CardTitle>Datastores and Proxmox jobs</CardTitle>
        <div className="text-xs text-muted mb-2">Each box is one datastore. A copy that lands in a namespace is pruned by its own job; a datastore-wide prune job covers the rest. Edit here only to adjust what a plan created.</div>
        <Retention servers={servers.map((s) => ({ id: s.id, name: s.name }))} jobs={backupJobs} />
      </Card>


    </div>
  );
}
