import { apiFetch } from "@/lib/api";
import type { Organization, Provider, PveTarget, Site } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import PveTargetForm from "@/components/PveTargetForm";
import PveTargetRow from "@/components/PveTargetRow";
import AddSiteForm from "@/components/AddSiteForm";
import SiteRow from "@/components/SiteRow";
import { PlugIcon } from "@/components/Icons";

const UNIMPLEMENTED_CATEGORIES = ["protection", "monitoring", "notification", "itsm", "authentication", "hardware"];

export default async function ProvidersPage() {
  const [providers, targets, sites, organizations] = await Promise.all([
    apiFetch<Provider[]>("/api/providers"),
    apiFetch<PveTarget[]>("/api/pve-targets"),
    apiFetch<Site[]>("/api/sites"),
    apiFetch<Organization[]>("/api/organizations"),
  ]);
  const organization = organizations[0];
  const pveProviders = providers.filter((p) => p.category_id === "pve");

  return (
    <div>
      <PageHeader
        title="Integrations"
        subtitle="Connect your infrastructure in two steps: add a site (a physical location or logical grouping), then connect a provider to it."
        icon={<PlugIcon className="w-5 h-5" />}
      />

      {organization && (
        <Card className="mb-4">
          <CardTitle>Step 1 · Sites</CardTitle>
          <div className="divide-y divide-border mb-3">
            {sites.map((s) => (
              <SiteRow key={s.id} site={s} />
            ))}
          </div>
          <AddSiteForm organizationId={organization.id} />
        </Card>
      )}

      <Card className="mb-4">
        <CardTitle>Step 2 · Proxmox VE (PVE targets)</CardTitle>
        <p className="text-xs text-muted mb-3 normal-case">
          The only provider category implemented so far -- connects one Proxmox cluster or standalone host per target.
        </p>
        {pveProviders.length === 0 && <div className="text-sm text-muted mb-3">No PVE provider configured yet.</div>}
        {pveProviders.map((p) => (
          <div key={p.id} className="mb-3 pb-3 border-b border-border last:border-0 last:pb-0 last:mb-0">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-text">{p.instance_name}</div>
              <StatusBadge status={p.connection_health} />
            </div>
            <div className="text-xs text-muted mt-1">
              contract v{p.contract_version} · {p.capabilities.join(", ") || "no capabilities declared"}
            </div>
            {p.last_error && <div className="text-xs text-bad mt-1">{p.last_error}</div>}
          </div>
        ))}

        <div className="mt-3 space-y-3">
          {targets.map((t) => (
            <PveTargetRow key={t.id} target={t} />
          ))}
        </div>

        <div className="mt-4">
          <PveTargetForm sites={sites} />
        </div>
      </Card>

      <Card className="opacity-70">
        <details>
          <summary className="cursor-pointer select-none">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">
              Other provider categories -- none implemented yet
            </span>
          </summary>
          <div className="text-sm text-muted mt-2">
            {UNIMPLEMENTED_CATEGORIES.map((c) => (
              <div key={c} className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
                <span className="capitalize">{c}</span>
                <span className="text-xs text-muted">Not configured</span>
              </div>
            ))}
          </div>
          <div className="text-xs text-muted mt-2">
            Reserved for future phases (PBS, Veeam, Commvault, etc). Nothing to set up here yet.
          </div>
        </details>
      </Card>
    </div>
  );
}
