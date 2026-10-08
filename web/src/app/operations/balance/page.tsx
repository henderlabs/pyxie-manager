import { apiFetch } from "@/lib/api";
import type { Node } from "@/lib/api";
import { PageHeader } from "@/components/Card";
import BalanceLoadWorkspace from "@/components/BalanceLoadWorkspace";
import { MigrateIcon } from "@/components/Icons";

export default async function BalanceLoadPage({ searchParams }: { searchParams: { node?: string } }) {
  const nodes = await apiFetch<Node[]>("/api/nodes");
  return (
    <div>
      <PageHeader
        title="Balance Load"
        subtitle="Preview only: nothing moves until you approve. Shows memory per node before and after the plan, and why any move was refused."
        icon={<MigrateIcon className="w-5 h-5" />}
      />
      <BalanceLoadWorkspace nodes={nodes} initialNodeId={searchParams.node} />
    </div>
  );
}
