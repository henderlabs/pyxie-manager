import { apiFetch } from "@/lib/api";
import type { AppSettings } from "@/lib/api";
import { Card, PageHeader } from "@/components/Card";
import SmtpSettingsForm from "@/components/SmtpSettingsForm";
import { MailIcon } from "@/components/Icons";

export default async function EmailSettingsPage() {
  const settings = await apiFetch<AppSettings>("/api/settings");

  return (
    <div>
      <PageHeader
        title="Email (SMTP)"
        subtitle="Settings · the outgoing mail server PyXie uses for notifications and user invites"
        icon={<MailIcon className="w-5 h-5" />}
      />
      <Card>
        <SmtpSettingsForm initial={settings} />
      </Card>
    </div>
  );
}
