import { apiFetch } from "@/lib/api";
import type { Organization, Provider, PveEndpoints, PveTarget, Site } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import PveTargetForm from "@/components/PveTargetForm";
import PveTargetRow from "@/components/PveTargetRow";
import AddSiteForm from "@/components/AddSiteForm";
import SiteRow from "@/components/SiteRow";
import { PlugIcon } from "@/components/Icons";
import HostSetupBuilder from "@/components/HostSetupBuilder";
import SetupGuide from "@/components/SetupGuide";
import type { SetupStatus } from "@/components/SetupGuide";

const UNIMPLEMENTED_CATEGORIES = ["protection", "monitoring", "notification", "itsm", "authentication", "hardware"];

export default async function ProvidersPage() {
  const [providers, targets, sites, organizations, setupStatus] = await Promise.all([
    apiFetch<Provider[]>("/api/providers"),
    apiFetch<PveTarget[]>("/api/pve-targets"),
    apiFetch<Site[]>("/api/sites"),
    apiFetch<Organization[]>("/api/organizations"),
    apiFetch<SetupStatus>("/api/setup/status"),
  ]);
  const endpointsByTarget: Record<string, PveEndpoints | null> = Object.fromEntries(
    await Promise.all(
      targets.map(
        async (t) => [t.id, await apiFetch<PveEndpoints>(`/api/pve-targets/${t.id}/endpoints`).catch(() => null)] as const,
      ),
    ),
  );
  const organization = organizations[0];
  const pveProviders = providers.filter((p) => p.category_id === "pve");

  return (
    <div>
      <PageHeader
        title="Integrations"
        subtitle="Follow the steps below from top to bottom. Each step says what to do, why, and whether it is already done."
        icon={<PlugIcon className="w-5 h-5" />}
      />

      <SetupGuide initial={setupStatus} />

      {organization && (
        <Card className="mb-4" id="sites">
          <CardTitle>Step 1 · Add a site</CardTitle>
          <div className="divide-y divide-border mb-3">
            {sites.map((s) => (
              <SiteRow key={s.id} site={s} />
            ))}
          </div>
          <AddSiteForm organizationId={organization.id} />
        </Card>
      )}

      <Card className="mb-4" id="builder">
        <CardTitle>Steps 2 and 5 · Setup scripts (Proxmox accounts, host wrapper)</CardTitle>
        <p className="text-xs text-muted mb-3 normal-case">
          Tick what you need and PyXie writes the scripts: the Proxmox service account, roles and tokens, and the host wrapper that lets PyXie patch and reboot a node. You copy them onto a node and run them yourself.
        </p>
        <HostSetupBuilder targets={targets.map((t) => ({ id: t.id, name: t.name }))} />
      </Card>

      <Card className="mb-4" id="targets">
        <CardTitle>Step 3 · Connect your cluster (PVE targets)</CardTitle>
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
            <PveTargetRow key={t.id} target={t} endpoints={endpointsByTarget[t.id] ?? null} />
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
