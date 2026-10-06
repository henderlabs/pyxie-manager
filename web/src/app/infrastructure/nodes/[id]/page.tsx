import Link from "next/link";
import { apiFetch } from "@/lib/api";
import type { HostMaintenanceStatus, Node, PveTask, Workload } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import NodeOverview from "@/components/NodeOverview";
import { formatBytes } from "@/lib/format";
import NodeWorkloadsTable from "@/components/tables/NodeWorkloadsTable";
import StatusBadge from "@/components/StatusBadge";
import { ServerIcon, WrenchIcon } from "@/components/Icons";

export default async function NodeDetailPage({ params }: { params: { id: string } }) {
  const [node, workloads, tasks, hostMaintenance] = await Promise.all([
    apiFetch<Node>(`/api/nodes/${params.id}`),
    apiFetch<Workload[]>(`/api/nodes/${params.id}/workloads`),
    apiFetch<PveTask[]>(`/api/nodes/${params.id}/tasks`),
    apiFetch<HostMaintenanceStatus>(`/api/nodes/${params.id}/host-maintenance-status`).catch(() => null),
  ]);

  return (
    <div>
      <div className="flex items-start justify-between gap-3">
        <PageHeader title={node.name} subtitle="Host · live status from PVE" icon={<ServerIcon className="w-5 h-5" />} />
        <Link
          href={`/operations/maintenance?node=${node.id}`}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium bg-ink text-white border border-warn hover:bg-warn/10 shrink-0"
          title="Open this node on the Maintenance page"
        >
          <WrenchIcon className="w-4 h-4" />
          Manage in Maintenance
        </Link>
      </div>

      <NodeOverview nodeId={params.id} initialNode={node} rebootRequired={hostMaintenance ? hostMaintenance.reboot_required ?? null : null} />

      <Card className="mb-4">
        <CardTitle>Recent Tasks</CardTitle>
        <div className="space-y-1 text-sm">
          {tasks.length === 0 && <div className="text-muted">No recent tasks.</div>}
          {tasks.slice(0, 8).map((t) => (
            <div key={t.id} className="flex justify-between">
              <span className="text-text">{t.task_type || t.upid.split(":")[1]}</span>
              <StatusBadge status={t.status || "unknown"} />
            </div>
          ))}
        </div>
      </Card>

      <Card className="mb-4">
        <CardTitle>Host Maintenance</CardTitle>
        {hostMaintenance ? <HostMaintenancePanel status={hostMaintenance} nodeId={params.id} /> : (
          <div className="text-sm text-muted py-2">Could not reach the readiness check.</div>
        )}
      </Card>

      <Card>
        <CardTitle>Workloads</CardTitle>
        <NodeWorkloadsTable workloads={workloads} />
      </Card>
    </div>
  );
}

function HostMaintenancePanel({ status, nodeId }: { status: HostMaintenanceStatus; nodeId: string }) {
  if (!status.provisioned) {
    const reason = !status.credential_configured
      ? "no host-maintenance SSH credential configured for this cluster"
      : !status.management_ip_known
      ? "no management_ip on record yet -- run discovery"
      : !status.host_key_pinned
      ? "SSH host key not pinned yet for this node"
      : status.error || "not reachable";
    return (
      <div className="text-sm">
        <StatusBadge status="unknown" />
        <span className="text-muted ml-2">Not provisioned — {reason}</span>
      </div>
    );
  }

  return (
    <div>
      {status.reboot_required && (
        <a
          href={`/operations/maintenance?node=${nodeId}&action=host-reboots`}
          className="block mb-3 px-3 py-2 rounded border border-warn bg-warn/10 text-warn text-sm font-medium hover:bg-warn/20"
        >
          Reboot required — a newer kernel or other change is installed but not yet running. Click to go reboot this node →
        </a>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 text-sm">
        <Row label="Reachable" value={status.reachable ? "yes" : `no${status.error ? ` — ${status.error}` : ""}`} />
        <Row label="Wrapper version" value={status.wrapper_version || "—"} />
        <Row
          label="Contract version"
          value={
            status.contract_version != null
              ? `${status.contract_version}${status.contract_compatible ? "" : " (incompatible)"}`
              : "—"
          }
        />
        <Row label="Running kernel" value={status.kernel_version || "—"} />
        <Row label="Packages upgradable" value={status.upgradable_count != null ? String(status.upgradable_count) : "—"} />
        <Row label="Reboot required" value={status.reboot_required == null ? "—" : status.reboot_required ? "yes" : "no"} />
        <Row label="Disk free" value={status.disk_free_bytes != null ? formatBytes(status.disk_free_bytes) : "—"} />
        <Row label="Host key fingerprint" value={status.host_key_fingerprint || "—"} />
        <Row label="Host key pinned" value={status.host_key_pinned_at ? new Date(status.host_key_pinned_at).toLocaleString() : "—"} />
        <Row label="Capabilities" value={(status.capabilities || []).join(", ") || "—"} />
        <Row label="Status checked" value={status.status_checked_at ? new Date(status.status_checked_at).toLocaleString() : "—"} />
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-muted">{label}</dt>
      <dd className="text-text">{value}</dd>
    </div>
  );
}
