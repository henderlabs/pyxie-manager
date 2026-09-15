import { apiFetch } from "@/lib/api";
import type { Finding, Node, Recommendation, RightsizingAssessment, Workload } from "@/lib/api";
import { PageHeader, StatTile } from "@/components/Card";
import WorkloadsTable from "@/components/tables/WorkloadsTable";
import { WorkloadIcon } from "@/components/Icons";
import { formatBytes } from "@/lib/format";

export default async function WorkloadsPage({
  searchParams,
}: {
  searchParams: { type?: string };
}) {
  const [workloads, nodes, findings, rightsizing, rightsizingRecs] = await Promise.all([
    apiFetch<Workload[]>(`/api/workloads${searchParams.type ? `?type=${searchParams.type}` : ""}`),
    apiFetch<Node[]>("/api/nodes"),
    apiFetch<Finding[]>("/api/findings?active=true"),
    apiFetch<RightsizingAssessment[]>("/api/rightsizing"),
    apiFetch<Recommendation[]>("/api/recommendations?category=rightsizing"),
  ]);

  // Excludes is_missing -- a removed guest's old allocation isn't real
  // capacity in use anymore. Summary tiles at the top of
  // Workloads/Hosts & Clusters/Storage.
  const present = workloads.filter((w) => !w.is_missing);
  const vmCount = present.filter((w) => w.type === "vm").length;
  const ctCount = present.filter((w) => w.type === "lxc").length;
  const allocatedVcpu = present.reduce((sum, w) => sum + (w.cpu_cores ?? 0), 0);
  const allocatedMemBytes = present.reduce((sum, w) => sum + (w.memory_bytes ?? 0), 0);

  return (
    <div>
      <PageHeader title="Workloads" subtitle="Unified VM + container inventory" icon={<WorkloadIcon className="w-5 h-5" />} />
      <div className="flex gap-2 mb-4 text-sm">
        <FilterLink href="/infrastructure/workloads" active={!searchParams.type} label="All" />
        <FilterLink href="/infrastructure/workloads?type=vm" active={searchParams.type === "vm"} label="VMs" />
        <FilterLink href="/infrastructure/workloads?type=lxc" active={searchParams.type === "lxc"} label="Containers" />
      </div>
      <div className="grid grid-cols-4 gap-3 mb-5">
        <StatTile label="VMs" value={vmCount} />
        <StatTile label="Containers" value={ctCount} />
        <StatTile label="Allocated vCPU" value={allocatedVcpu} />
        <StatTile label="Allocated RAM" value={formatBytes(allocatedMemBytes)} />
      </div>
      <WorkloadsTable
        workloads={workloads}
        nodes={nodes}
        findings={findings}
        rightsizing={rightsizing}
        rightsizingRecs={rightsizingRecs}
      />
    </div>
  );
}

function FilterLink({ href, active, label }: { href: string; active: boolean; label: string }) {
  return (
    <a
      href={href}
      className={`px-3 py-1 rounded border ${
        active ? "border-accent text-accent bg-accent/10" : "border-border text-muted hover:text-text"
      }`}
    >
      {label}
    </a>
  );
}
