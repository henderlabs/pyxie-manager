"use client";

import ThemeToggle from "@/components/ThemeToggle";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  DashboardIcon, HealthIcon, LightbulbIcon, WrenchIcon, LinkIcon, ShieldIcon, GaugeIcon,
  ServerIcon, WorkloadIcon, StorageIcon, NetworkIcon, PlugIcon, KeyIcon,
  ScrollIcon, ReportIcon, SlidersIcon, GearIcon, UsersIcon, BellIcon, MailIcon,
} from "@/components/Icons";

type NavItem = {
  label: string;
  href: string;
  icon: React.ReactNode;
  comingSoon?: boolean;
  adminOnly?: boolean;
  // Nested pages, shown indented under this item while any page in the
  // group is open. `exact` keeps the parent itself from also lighting up
  // for its children's routes (which share its URL prefix).
  children?: NavItem[];
  exact?: boolean;
};
type NavSection = { label: string; items: NavItem[] };

// Any page with an actionable review queue gets a badge here, sourced from
// a small summary endpoint (never the full list -- this is client-side and
// can't call the backend directly). Add a new entry any time a new page
// grows its own "needs attention" queue.
const BADGE_SOURCES: Record<string, string> = {
  "/operations/health": "/api/findings-summary",
  "/operations/maintenance": "/api/operations-summary",
  "/operations/recommendations": "/api/recommendations-summary",
  "/infrastructure/nodes": "/api/nodes-summary",
};

const SECTIONS: NavSection[] = [
  {
    label: "",
    items: [{ label: "Dashboard", href: "/", icon: <DashboardIcon /> }],
  },
  {
    label: "Operations",
    items: [
      { label: "Health", href: "/operations/health", icon: <HealthIcon /> },
      { label: "Rightsizing", href: "/operations/recommendations", icon: <LightbulbIcon /> },
      { label: "Maintenance", href: "/operations/maintenance", icon: <WrenchIcon /> },
      { label: "Affinity Rules", href: "/operations/affinity-rules", icon: <LinkIcon /> },
      { label: "Protection", href: "/operations/protection", icon: <ShieldIcon /> },
      { label: "Capacity", href: "/operations/capacity", icon: <GaugeIcon /> },
    ],
  },
  {
    label: "Infrastructure",
    items: [
      { label: "Hosts & Clusters", href: "/infrastructure/nodes", icon: <ServerIcon /> },
      { label: "Workloads", href: "/infrastructure/workloads", icon: <WorkloadIcon /> },
      { label: "Storage", href: "/infrastructure/storage", icon: <StorageIcon /> },
      { label: "Network", href: "/infrastructure/network", icon: <NetworkIcon /> },
    ],
  },
  {
    label: "Platform",
    items: [
      { label: "Logging", href: "/platform/logging", icon: <ScrollIcon /> },
      { label: "Reporting", href: "/platform/reporting", icon: <ReportIcon /> },
      {
        label: "Settings",
        href: "/platform/settings",
        icon: <GearIcon />,
        exact: true,
        children: [
          { label: "Notifications", href: "/platform/settings/notifications", icon: <BellIcon /> },
          { label: "Email (SMTP)", href: "/platform/settings/email", icon: <MailIcon /> },
          { label: "Integrations", href: "/platform/providers", icon: <PlugIcon /> },
          { label: "Credentials", href: "/platform/credentials", icon: <KeyIcon /> },
          { label: "Users", href: "/platform/users", icon: <UsersIcon />, adminOnly: true },
          { label: "Policies", href: "/platform/policies", icon: <SlidersIcon /> },
        ],
      },
    ],
  },
];

export default function Sidebar({ version, instance }: { version: string; instance: string }) {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<{ email: string; display_name: string | null; is_admin: boolean } | null>(null);
  const [badges, setBadges] = useState<Record<string, number>>({});
  const isPublicPage = pathname === "/login" || pathname.startsWith("/accept-invite");

  // Every page's own data is server-rendered fresh on each navigation
  // (apiFetch uses cache: "no-store"), so a route change is exactly when
  // "last refreshed" should reset. `now` just ticks periodically so the
  // relative-time display ("2m ago") stays roughly current without
  // needing a click.
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (isPublicPage) return;
    setLastRefreshed(new Date());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(t);
  }, []);

  function relativeTime(d: Date): string {
    const secs = Math.max(0, Math.round((now - d.getTime()) / 1000));
    if (secs < 5) return "just now";
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    return `${Math.round(mins / 60)}h ago`;
  }

  function refreshNow() {
    router.refresh();
    setLastRefreshed(new Date());
  }

  useEffect(() => {
    if (isPublicPage) return;
    fetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then(setMe)
      .catch(() => setMe(null));
  }, [pathname]);

  useEffect(() => {
    if (isPublicPage) return;
    function refresh() {
      Object.entries(BADGE_SOURCES).forEach(([href, endpoint]) => {
        fetch(endpoint)
          .then((r) => (r.ok ? r.json() : { count: 0 }))
          .then((d) => setBadges((b) => ({ ...b, [href]: d.count || 0 })))
          .catch(() => {});
      });
    }
    refresh();
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, [pathname]);

  if (isPublicPage) return null;

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  function renderLink(item: NavItem, nested: boolean) {
    // Sub-routes (e.g. /infrastructure/nodes/<id>, a node detail page
    // nested under the Hosts & Clusters item) should still light up their
    // parent nav item, not just an exact match.
    const active = item.exact
      ? pathname === item.href
      : pathname === item.href || pathname.startsWith(item.href + "/");
    if (item.comingSoon) {
      return (
        <div
          key={item.href}
          title="Not built yet -- no network topology to show in this lab"
          className="flex items-center justify-between px-4 py-1.5 mx-2 rounded text-sm text-muted/50 cursor-default"
        >
          <span className="flex items-center gap-2">
            <span className="text-muted/40">{item.icon}</span>
            {item.label}
          </span>
          <span className="text-[9px] uppercase tracking-wide border border-border rounded px-1 py-0.5 text-muted/60">
            soon
          </span>
        </div>
      );
    }
    return (
      <Link
        key={item.href}
        href={item.href}
        className={`flex items-center justify-between py-1.5 mx-2 rounded text-sm ${nested ? "pl-9 pr-4" : "px-4"} ${
          active ? "bg-accent/15 text-accent font-medium" : "text-text/80 hover:bg-surface2 hover:text-text"
        }`}
      >
        <span className="flex items-center gap-2">
          <span className={active ? "text-accent" : "text-muted"}>{item.icon}</span>
          {item.label}
        </span>
        {(badges[item.href] || 0) > 0 && (
          <span className="text-proxmox text-xs font-bold leading-none">{badges[item.href]}</span>
        )}
      </Link>
    );
  }

  return (
    <aside className="w-60 shrink-0 border-r border-border bg-surface h-screen sticky top-0 flex flex-col">
      <Link href="/" className="px-4 py-5 border-b border-border flex items-center justify-center">
        <img src="/pyxie-logo.png" alt="PyXie — Proxmox Operations" className="logo-for-dark h-12 w-auto" />
        <img src="/pyxie-logo-light.png" alt="PyXie — Proxmox Operations" className="logo-for-light h-12 w-auto" />
      </Link>
      <nav className="flex-1 overflow-y-auto pb-3">
        {SECTIONS.map((section) => (
          <div key={section.label || "root"} className="mb-3">
            {section.label && (
              <div className="px-4 pt-2 pb-1 text-[11px] font-bold uppercase tracking-wider text-proxmox">
                {section.label}
              </div>
            )}
            {section.items.filter((item) => !item.adminOnly || me?.is_admin).map((item) => {
              const groupOpen =
                !!item.children &&
                (pathname === item.href ||
                  pathname.startsWith(item.href + "/") ||
                  item.children.some((c) => pathname === c.href || pathname.startsWith(c.href + "/")));
              return (
                <div key={item.href}>
                  {renderLink(item, false)}
                  {groupOpen &&
                    item.children!
                      .filter((c) => !c.adminOnly || me?.is_admin)
                      .map((c) => renderLink(c, true))}
                </div>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="px-4 py-3 border-t border-border text-xs">
        <div className="mb-2.5">
          <ThemeToggle />
        </div>
        {me ? (
          <div className="flex items-center justify-between gap-2">
            <Link href="/profile" className="text-muted hover:text-text truncate" title={`${me.email} — edit profile`}>
              {me.display_name || me.email}
            </Link>
            <button onClick={logout} className="text-accent hover:underline shrink-0">
              Sign out
            </button>
          </div>
        ) : (
          <span className="text-muted">Read-only</span>
        )}
      </div>
      <div className="px-4 py-1.5 flex items-center justify-between text-xs text-muted/70 tracking-wide">
        <span className="truncate min-w-0" title={`${instance} v${version}`}>{instance} v{version}</span>
        {lastRefreshed && (
          <button
            onClick={refreshNow}
            title={`Page data as of ${lastRefreshed.toLocaleTimeString()} — click to refresh`}
            className="hover:text-text"
          >
            ↻ {relativeTime(lastRefreshed)}
          </button>
        )}
      </div>
    </aside>
  );
}
