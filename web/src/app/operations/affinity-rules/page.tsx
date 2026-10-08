import { apiFetch } from "@/lib/api";
import type { Node, Workload } from "@/lib/api";
import { PageHeader } from "@/components/Card";
import AffinityRuleManager from "@/components/AffinityRuleManager";
import { LinkIcon } from "@/components/Icons";

type AffinityRule = {
  id: string;
  rule_type: string;
  scope_type: string;
  workload_ids: string[] | null;
  tag: string;
  strict: boolean;
  description: string | null;
  created_by: string | null;
};

export default async function AffinityRulesPage() {
  const [rules, workloads, nodes] = await Promise.all([
    apiFetch<AffinityRule[]>("/api/placement/affinity-rules"),
    apiFetch<Workload[]>("/api/workloads"),
    apiFetch<Node[]>("/api/nodes"),
  ]);

  return (
    <div>
      <PageHeader
        title="Affinity Rules"
        subtitle="Rules PyXie checks before it places or moves a guest. They are separate from Proxmox's own HA affinity rules."
        icon={<LinkIcon className="w-5 h-5" />}
      />
      <AffinityRuleManager initialRules={rules} workloads={workloads} nodes={nodes} />
    </div>
  );
}
