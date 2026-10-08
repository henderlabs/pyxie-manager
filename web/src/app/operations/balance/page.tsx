import { apiFetch } from "@/lib/api";
import type { Node, Workload } from "@/lib/api";
import { PageHeader } from "@/components/Card";
import BalanceLoadWorkspace from "@/components/BalanceLoadWorkspace";
import { MigrateIcon } from "@/components/Icons";

export default async function BalanceLoadPage({ searchParams }: { searchParams: { node?: string } }) {
  const [nodes, workloads] = await Promise.all([apiFetch<Node[]>("/api/nodes"), apiFetch<Workload[]>("/api/workloads")]);
  return (
    <div>
      <PageHeader
        title="Balance Load"
        subtitle="Preview only: nothing moves until you approve. Shows memory per node before and after the plan, and why any move was refused."
        icon={<MigrateIcon className="w-5 h-5" />}
      />
      <BalanceLoadWorkspace nodes={nodes} workloads={workloads} initialNodeId={searchParams.node} />
    </div>
  );
}
