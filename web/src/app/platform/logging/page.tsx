import { apiFetch } from "@/lib/api";
import type { AuditEvent, Cluster, InternalJobRunRow, Node, Provider, PveTask, Site, Workload } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import AuditLogTable from "@/components/tables/AuditLogTable";
import PveTasksTable from "@/components/tables/PveTasksTable";
import InternalJobsTable from "@/components/tables/InternalJobsTable";
import { ScrollIcon } from "@/components/Icons";

type Channel = "audit" | "tasks" | "jobs";

// Three genuinely different-shaped logs (structured audit envelope vs raw
// PVE task history vs PyXie's own background job runs) kept as distinct
// channels rather than one merged table -- same idea as Windows Event
// Viewer's Application/System/Security logs: one page, switchable logs,
// each row expandable for the full record instead of a wall of raw JSON.
// Audit Log was Platform's own page before this; Tasks used to live
// under Operations -- both retired in
// favor of this one, redirecting old links here.
const CHANNELS: { id: Channel; label: string }[] = [
  { id: "audit", label: "Audit Log" },
  { id: "tasks", label: "PVE Tasks" },
  { id: "jobs", label: "Internal Jobs" },
];

export default async function LoggingPage({
  searchParams,
}: {
  searchParams: { channel?: string; event_category?: string; result?: string };
}) {
  const channel: Channel = CHANNELS.some((c) => c.id === searchParams.channel) ? (searchParams.channel as Channel) : "audit";

  const [nodes, workloads, clusters, sites, providers] = await Promise.all([
    apiFetch<Node[]>("/api/nodes"),
    apiFetch<Workload[]>("/api/workloads"),
    apiFetch<Cluster[]>("/api/clusters"),
    apiFetch<Site[]>("/api/sites"),
    apiFetch<Provider[]>("/api/providers"),
  ]);
  const nodeNameById = Object.fromEntries(nodes.map((n) => [n.id, n.name]));
  const workloadNameById = Object.fromEntries(workloads.map((w) => [w.id, w.name || `vmid ${w.vmid}`]));
  const clusterNameById = Object.fromEntries(clusters.map((c) => [c.id, c.name]));
  const siteNameById = Object.fromEntries(sites.map((s) => [s.id, s.name]));
  const providerNameById = Object.fromEntries(providers.map((p) => [p.id, p.instance_name]));

  return (
    <div>
      <PageHeader
        title="Logging"
        subtitle="Audit Log, PVE Tasks, and PyXie's own background jobs -- click a row's ▸ for the full record"
        icon={<ScrollIcon className="w-5 h-5" />}
      />

      <div className="flex gap-2 mb-4 text-sm">
        {CHANNELS.map((c) => (
          <a
            key={c.id}
            href={`/platform/logging?channel=${c.id}`}
            className={`px-3 py-1 rounded border ${
              channel === c.id ? "border-accent text-accent bg-accent/10" : "border-border text-muted hover:text-text"
            }`}
          >
            {c.label}
          </a>
        ))}
      </div>

      {channel === "audit" && (
        <AuditChannel
          searchParams={searchParams}
          nodeNameById={nodeNameById}
          workloadNameById={workloadNameById}
          clusterNameById={clusterNameById}
          siteNameById={siteNameById}
          providerNameById={providerNameById}
        />
      )}
      {channel === "tasks" && <TasksChannel nodeNameById={nodeNameById} clusterNameById={clusterNameById} />}
      {channel === "jobs" && <JobsChannel />}
    </div>
  );
}

async function AuditChannel({
  searchParams,
  nodeNameById,
  workloadNameById,
  clusterNameById,
  siteNameById,
  providerNameById,
}: {
  searchParams: { event_category?: string; result?: string };
  nodeNameById: Record<string, string>;
  workloadNameById: Record<string, string>;
  clusterNameById: Record<string, string>;
  siteNameById: Record<string, string>;
  providerNameById: Record<string, string>;
}) {
  const params = new URLSearchParams();
  if (searchParams.event_category) params.set("event_category", searchParams.event_category);
  if (searchParams.result) params.set("result", searchParams.result);
  params.set("limit", "200");

  const events = await apiFetch<AuditEvent[]>(`/api/audit-events?${params.toString()}`);

  return (
    <>
      <form className="flex gap-2 mb-4 text-sm" action="/platform/logging">
        <input type="hidden" name="channel" value="audit" />
        <select name="event_category" defaultValue={searchParams.event_category || ""} className="select">
          <option value="">All categories</option>
          <option value="system">System</option>
          <option value="inventory">Inventory</option>
          <option value="provider">Provider</option>
          <option value="credential">Credential</option>
          <option value="settings">Settings</option>
        </select>
        <select name="result" defaultValue={searchParams.result || ""} className="select">
          <option value="">All results</option>
          <option value="success">Success</option>
          <option value="failure">Failure</option>
          <option value="error">Error</option>
        </select>
        <button type="submit" className="px-3 py-1.5 rounded bg-surface2 border border-border text-text">
          Filter
        </button>
      </form>
      <AuditLogTable
        events={events}
        nodeNameById={nodeNameById}
        workloadNameById={workloadNameById}
        clusterNameById={clusterNameById}
        siteNameById={siteNameById}
        providerNameById={providerNameById}
      />
      <style>{`.select { background:#161b26; border:1px solid #232a38; border-radius:6px; padding:6px 8px; color:#e6e9ef; }`}</style>
    </>
  );
}

async function TasksChannel({
  nodeNameById,
  clusterNameById,
}: {
  nodeNameById: Record<string, string>;
  clusterNameById: Record<string, string>;
}) {
  const tasks = await apiFetch<PveTask[]>("/api/tasks?limit=100");
  const failedTasks = tasks.filter((t) => t.status && t.status !== "OK").length;
  return (
    <Card>
      <CardTitle>PVE Tasks {failedTasks > 0 && <span className="text-bad">({failedTasks} not OK)</span>}</CardTitle>
      <PveTasksTable tasks={tasks} nodeNameById={nodeNameById} clusterNameById={clusterNameById} />
    </Card>
  );
}

async function JobsChannel() {
  const jobs = await apiFetch<InternalJobRunRow[]>("/api/internal-jobs?limit=50");
  return (
    <Card>
      <CardTitle>PyXie Internal Jobs</CardTitle>
      <InternalJobsTable jobs={jobs} />
    </Card>
  );
}
