import { apiFetch } from "@/lib/api";
import type { BackupJob } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import BackupPlans from "@/components/BackupPlans";
import ProtectionGuests, { type GuestRow } from "@/components/ProtectionGuests";
import { ShieldIcon } from "@/components/Icons";

type Scorecard = { summary: { ok: number; partial: number; none: number; total: number }; rows: GuestRow[]; stores: { name: string }[]; errors: string[] };

export default async function ProtectionPage() {
  const [card, backupJobs] = await Promise.all([
    apiFetch<Scorecard>("/api/protection/scorecard"),
    apiFetch<BackupJob[]>("/api/protection/backup-jobs"),
  ]);
  return (
    <div>
      <PageHeader title="Protection" subtitle="One backup plan says how every guest is backed up, copied, kept and checked. Exceptions are picked per guest below, where you also restore." icon={<ShieldIcon className="w-5 h-5" />} />
      {card.errors.map((e) => <div key={e} className="text-warn text-sm mb-2">{e}</div>)}

      <div className="grid grid-cols-3 gap-4 mb-4">
        <Card className="!p-3"><div className="text-xs text-muted">3-2-1 met</div><div className="text-2xl font-semibold text-good leading-tight">{card.summary.ok}<span className="text-xs text-muted font-normal"> of {card.summary.total} guests</span></div></Card>
        <Card className="!p-3"><div className="text-xs text-muted">Partly protected</div><div className="text-2xl font-semibold text-warn leading-tight">{card.summary.partial}<span className="text-xs text-muted font-normal"> a copy missing or old</span></div></Card>
        <Card className="!p-3"><div className="text-xs text-muted">Not protected</div><div className="text-2xl font-semibold text-bad leading-tight">{card.summary.none}<span className="text-xs text-muted font-normal"> no recent backup</span></div></Card>
      </div>

      <BackupPlans />

      <Card className="mb-4">
        <CardTitle>Guests</CardTitle>
        <div className="text-xs text-muted mb-2">Open a guest to see its plan (and pick a different one), every restore point on site and offsite, and to restore from one.</div>
        <ProtectionGuests rows={card.rows} stores={card.stores.map((s) => s.name)} jobs={backupJobs} />
      </Card>
    </div>
  );
}
