import { apiFetch } from "@/lib/api";
import type { AppSettings, Organization } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import SettingsForm from "@/components/SettingsForm";
import OrganizationForm from "@/components/OrganizationForm";
import { GearIcon } from "@/components/Icons";

export default async function SettingsPage() {
  const [settings, organizations] = await Promise.all([
    apiFetch<AppSettings>("/api/settings"),
    apiFetch<Organization[]>("/api/organizations"),
  ]);
  const organization = organizations[0];

  return (
    <div>
      <PageHeader title="Settings" icon={<GearIcon className="w-5 h-5" />} />
      {organization && (
        <Card className="mb-4">
          <CardTitle>Organization</CardTitle>
          <div className="text-xs text-muted mb-3">
            This deployment&apos;s company name — shown throughout the app and used when onboarding a new site or
            PVE environment.
          </div>
          <OrganizationForm initial={organization} />
        </Card>
      )}
      <Card>
        <SettingsForm initial={settings} />
      </Card>
    </div>
  );
}
