import Link from "next/link";
import { apiFetch } from "@/lib/api";
import type { AuditEvent, DashboardSummary, Finding, ProtectionResultRow, Recommendation } from "@/lib/api";
import { Card, CardTitle, PageHeader, StatTile } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import RebootBanner from "@/components/RebootBanner";
import DashboardNodesList from "@/components/DashboardNodesList";
import { RadialGauge } from "@/components/Gauges";
import { ClusterIcon, ServerIcon, WorkloadIcon, StorageIcon, PackageIcon, CpuIcon, MemoryIcon, DashboardIcon, ShieldIcon, HealthIcon, WrenchIcon, ChecklistIcon } from "@/components/Icons";
import { formatPct } from "@/lib/format";

export default async function DashboardPage() {
  // Deliberately only cheap DB reads here. reboot-required (RebootBanner,
  // below) used to be fetched server-side too -- a real SSH probe per node
  // (1-3+ seconds each, measured 2026-09-12), blocking the entire
  // Dashboard on it. Isolated into its own client component instead, same
  // fix already applied to Nodes/Maintenance.
  const [summary, findings, activity, recommendations, protectionResults] = await Promise.all([
    apiFetch<DashboardSummary>("/api/dashboard/summary"),
    apiFetch<Finding[]>("/api/findings?active=true"),
    apiFetch<AuditEvent[]>("/api/audit-events?limit=8"),
    apiFetch<Recommendation[]>("/api/recommendations"),
    apiFetch<ProtectionResultRow[]>("/api/protection/results"),
  ]);

  const issueCount = findings.filter((f) => f.severity === "critical" || f.severity === "warning").length;
  const topFindings = [...findings]
    .filter((f) => f.severity === "critical" || f.severity === "warning")
    .sort((a, b) => (a.severity === "critical" ? 0 : 1) - (b.severity === "critical" ? 0 : 1))
    .slice(0, 5);
  const topRecommendations = [...recommendations]
    .sort((a, b) => (severityRank(a.severity) - severityRank(b.severity)))
    .slice(0, 5);

  const protectedCount = protectionResults.filter((r) => r.protected === "true").length;
  const unprotectedCount = protectionResults.filter((r) => r.protected === "false").length;
  const unknownCount = protectionResults.filter((r) => r.protected === "unknown").length;
  const hasProtectionData = protectionResults.length > 0;

  const cpuValues = summary.nodes_detail.map((n) => n.cpu_usage_pct).filter((v): v is number => v !== null);
  const memValues = summary.nodes_detail.map((n) => n.mem_usage_pct).filter((v): v is number => v !== null);
  const avgCpu = cpuValues.length ? cpuValues.reduce((a, b) => a + b, 0) / cpuValues.length : null;
  const avgMem = memValues.length ? memValues.reduce((a, b) => a + b, 0) / memValues.length : null;
  const workloadPct = summary.workloads_total > 0 ? (summary.workloads_running / summary.workloads_total) * 100 : null;
  const nodesInMaintenance = summary.nodes_detail.filter((n) => n.maintenance_mode);

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="What needs my attention right now?" icon={<DashboardIcon className="w-5 h-5" />} />

      <div className="flex flex-wrap gap-3 mb-4 empty:mb-0">
        {nodesInMaintenance.length > 0 && (
          <Card className="border-warn/40 flex-1 min-w-[280px]">
            <div className="flex items-center gap-2 text-sm text-warn">
              <WrenchIcon className="w-4 h-4" />
              <span className="font-medium">
                {nodesInMaintenance.map((n) => n.name).join(", ")} {nodesInMaintenance.length === 1 ? "is" : "are"} in maintenance mode
              </span>
              <Link href="/operations/maintenance" className="text-xs text-accent hover:underline ml-auto">
                Go to Maintenance →
              </Link>
            </div>
          </Card>
        )}
        <RebootBanner nodes={summary.nodes_detail.map((n) => ({ id: n.id, name: n.name }))} />
      </div>

      <Card className="mb-4">
        <CardTitle>Cluster Resource Usage</CardTitle>
        <div className="flex flex-wrap items-center justify-around gap-4 py-1">
          <RadialGauge value={avgCpu} label="Avg CPU across nodes" icon={<CpuIcon />} />
          <RadialGauge value={avgMem} label="Avg Memory across nodes" icon={<MemoryIcon />} />
          <RadialGauge value={summary.storage_used_pct} label="Storage used" icon={<StorageIcon />} />
          <RadialGauge value={workloadPct} label="Workloads running" icon={<WorkloadIcon />} />
        </div>
      </Card>

      <Card className="mb-4">
        <CardTitle>Environment Health</CardTitle>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
          <Metric label="Clusters" value={summary.counts.clusters} icon={<ClusterIcon />} />
          <Metric label="Nodes" value={summary.counts.nodes} icon={<ServerIcon />} />
          <Metric label="VMs" value={summary.counts.vms} icon={<WorkloadIcon />} />
          <Metric label="Containers" value={summary.counts.containers} icon={<WorkloadIcon />} />
          <Metric label="Storage" value={summary.counts.storage} icon={<StorageIcon />} />
        </div>
      </Card>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mb-4">
        <StatTile label="Cluster Status" value={<StatusBadge status={summary.cluster_status} />} icon={<ClusterIcon />} />
        <StatTile label="Nodes" value={`${summary.nodes_online} / ${summary.nodes_total}`} sub="Online" icon={<ServerIcon />} />
        <StatTile label="Workloads" value={`${summary.workloads_running} / ${summary.workloads_total}`} sub="Running" icon={<WorkloadIcon />} />
        <StatTile label="Storage" value={formatPct(summary.storage_used_pct)} sub="Used" icon={<StorageIcon />} />
        <StatTile label="Updates" value={summary.nodes_with_updates} sub="Nodes have updates" icon={<PackageIcon />} />
        <StatTile label="Active Findings" value={issueCount} sub="Critical + warning" icon={<HealthIcon />} />
        <StatTile
          label="Awaiting Approval"
          value={summary.operations_awaiting_approval}
          sub="Operations"
          icon={<ChecklistIcon />}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <Card className={topFindings.length > 0 ? "border-warn/40" : ""}>
          <CardTitle>Needs Attention</CardTitle>
          {topFindings.length === 0 ? (
            <div className="text-sm text-muted py-2">Nothing needs attention right now.</div>
          ) : (
            <div className="divide-y divide-border">
              {topFindings.map((f) => (
                <div key={f.id} className="flex items-center justify-between py-2 text-sm gap-2">
                  <div className="min-w-0">
                    <span className="text-text">{f.title}</span>
                    <span className="text-muted text-xs uppercase ml-2">{f.category}</span>
                  </div>
                  <StatusBadge status={f.severity} />
                </div>
              ))}
            </div>
          )}
          <div className="mt-2">
            <Link href="/operations/health" className="text-xs text-accent hover:underline">
              View all findings →
            </Link>
          </div>
        </Card>

        <Card>
          <CardTitle>Top Recommendations</CardTitle>
          {topRecommendations.length === 0 ? (
            <div className="text-sm text-muted py-2">No open recommendations right now.</div>
          ) : (
            <div className="divide-y divide-border">
              {topRecommendations.map((r) => (
                <div key={r.id} className="flex items-center justify-between py-2 text-sm gap-2">
                  <span className="text-text min-w-0">{r.title}</span>
                  <StatusBadge status={r.severity} />
                </div>
              ))}
            </div>
          )}
          <div className="mt-2">
            <Link href="/operations/recommendations" className="text-xs text-accent hover:underline">
              View all recommendations →
            </Link>
          </div>
        </Card>
      </div>

      <Card className="mb-4">
        <CardTitle>Protection</CardTitle>
        {hasProtectionData ? (
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <div className="text-xs text-muted flex items-center justify-center gap-1 mb-1">
                <ShieldIcon className="w-4 h-4 text-good/70" />
                Protected
              </div>
              <div className="text-xl font-semibold text-good">{protectedCount}</div>
            </div>
            <div>
              <div className="text-xs text-muted flex items-center justify-center gap-1 mb-1">
                <ShieldIcon className="w-4 h-4 text-bad/70" />
                Unprotected
              </div>
              <div className="text-xl font-semibold text-bad">{unprotectedCount}</div>
            </div>
            <div>
              <div className="text-xs text-muted flex items-center justify-center gap-1 mb-1">
                <ShieldIcon className="w-4 h-4 text-muted/70" />
                Unknown
              </div>
              <div className="text-xl font-semibold text-muted">{unknownCount}</div>
            </div>
          </div>
        ) : (
          <div className="text-sm text-muted py-2">No protection provider configured yet -- see Operations → Protection.</div>
        )}
      </Card>

      <Card className="mb-4">
        <CardTitle>Nodes</CardTitle>
        <DashboardNodesList initialNodes={summary.nodes_detail} />
      </Card>

      <Card>
        <CardTitle>Recent Activity</CardTitle>
        <div className="divide-y divide-border">
          {activity.length === 0 && <div className="text-sm text-muted py-4">No activity recorded yet.</div>}
          {activity.map((event) => (
            <div key={event.id} className="flex items-center justify-between py-2 text-sm">
              <span className="text-text">{describeEvent(event)}</span>
              <span className="text-muted text-xs">{new Date(event.timestamp).toLocaleString()}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function severityRank(s: string): number {
  return { critical: 0, warning: 1, info: 2 }[s] ?? 3;
}

function Metric({ label, value, icon }: { label: string; value: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-muted flex items-center gap-1 mb-1">
        {icon && <span className="text-proxmox/70">{icon}</span>}
        {label}
      </div>
      <div className="text-2xl font-semibold text-text">{value}</div>
    </div>
  );
}

function describeEvent(event: AuditEvent): string {
  const map: Record<string, string> = {
    "application.startup": "Application started",
    "inventory.discovery.started": "Inventory synchronization started",
    "inventory.discovery.completed": "Inventory synchronized",
    "inventory.discovery.failed": "Inventory synchronization failed",
    "pve_target.created": "PVE target created",
    "pve_target.connection_tested": "PVE connection tested",
    "credential.created": "Credential created",
    "settings.changed": "Settings changed",
    "auth.login.success": "User signed in",
    "auth.login.failed": "Failed sign-in attempt",
    "auth.logout": "User signed out",
    "protection.sync.completed": "Protection status synchronized",
    "protection.sync.failed": "Protection sync failed",
    "maintenance_plan.generated": "Maintenance plan generated",
    "policy.changed": "Policy changed",
  };
  return map[event.event_type] || event.event_type.replace(/_/g, " ").replace(/\./g, " ");
}
