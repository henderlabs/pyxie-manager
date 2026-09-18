import { apiFetch } from "@/lib/api";
import type { AppSettings, NotificationCatalog, NotificationRule, NotificationItem } from "@/lib/api";
import { Card, CardTitle, EmptyState, PageHeader } from "@/components/Card";
import Link from "next/link";
import NotificationRules from "@/components/NotificationRules";
import HoldDownSetting from "@/components/HoldDownSetting";
import StatusBadge from "@/components/StatusBadge";
import { GearIcon } from "@/components/Icons";

export default async function NotificationsSettingsPage() {
  const [settings, rules, catalog, recent] = await Promise.all([
    apiFetch<AppSettings>("/api/settings"),
    apiFetch<NotificationRule[]>("/api/notification-rules"),
    apiFetch<NotificationCatalog>("/api/notification-rules/catalog"),
    apiFetch<NotificationItem[]>("/api/notifications"),
  ]);

  return (
    <div>
      <PageHeader
        title="Notifications"
        subtitle="Settings · choose which events send email, and to whom"
        icon={<GearIcon className="w-5 h-5" />}
      />
      <Card className="mb-4">
        <CardTitle>Notification rules</CardTitle>
        <div className="text-xs text-muted mb-3">
          Each rule picks a set of events, a minimum severity, and who to email. An event that matches several rules
          still emails each address once. Every warning or critical event is also recorded below regardless of rules.
          {!settings.smtp_enabled && (
            <span className="text-warn"> Email is currently turned off — enable it under <Link href="/platform/settings/email" className="underline">Settings → Email (SMTP)</Link> or no rule will send anything.</span>
          )}
        </div>
        <HoldDownSetting initial={settings.notification_hold_down_minutes} />
        <NotificationRules initial={rules} catalog={catalog} />
      </Card>
      <Card>
        <CardTitle>Recent notifications</CardTitle>
        {recent.length === 0 ? (
          <EmptyState message="No notifications yet." />
        ) : (
          <div className="divide-y divide-border">
            {recent.slice(0, 50).map((n) => (
              <div key={n.id} className="py-2 text-sm flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  <StatusBadge status={n.severity === "informational" ? "healthy" : n.severity} />
                  <span className="text-text truncate">{n.title}</span>
                </div>
                <span className="text-xs text-muted shrink-0">{new Date(n.created_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
