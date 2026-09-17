import { apiFetch } from "@/lib/api";
import type { Node, Recommendation, RightsizingAssessment } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import RecommendationsList from "@/components/RecommendationsList";
import RightsizingTable from "@/components/tables/RightsizingTable";
import RecomputeRightsizingButton from "@/components/RecomputeRightsizingButton";
import { LightbulbIcon } from "@/components/Icons";

// Narrowed to VM/CT right-sizing only -- the cluster/
// node-level categories (updates, capacity, load-balancing) moved to the
// Maintenance page's own Recommendations column, since those are
// maintenance actions, not per-workload sizing ones.
//
// Table first, details second: the table is now the
// primary scan-and-act surface -- its Suggestion column IS the apply
// action (click the suggestion itself). The cards below stay for
// full context (benefit/impact/severity) and Acknowledge/Dismiss only;
// they no longer render their own Apply button for rightsizing, since
// that would just duplicate the table's.
export default async function RecommendationsPage() {
  const [recommendations, rightsizing, nodes, status] = await Promise.all([
    apiFetch<Recommendation[]>("/api/recommendations?category=rightsizing"),
    apiFetch<RightsizingAssessment[]>("/api/rightsizing"),
    apiFetch<Node[]>("/api/nodes"),
    apiFetch<{ computed_at: string | null }>("/api/rightsizing/status"),
  ]);

  return (
    <div>
      <PageHeader
        title="Rightsizing"
        subtitle="VM/CT sizing suggestions from observed usage -- explainable, evidence-backed, nothing here executes automatically"
        icon={<LightbulbIcon className="w-5 h-5" />}
      />
      <p className="text-xs text-muted -mt-4 mb-5">
        Historical analysis -- CPU/RAM observed over days to weeks, refreshed automatically every few minutes in the
        background (or on demand below). Not live like the Workloads page&apos;s usage meters; see there for
        real-time CPU/RAM.
      </p>

      <Card className="mb-4">
        <div className="flex items-center justify-between mb-2">
          <CardTitle>Rightsizing -- per-workload observation status</CardTitle>
          <RecomputeRightsizingButton computedAt={status.computed_at} />
        </div>
        <RightsizingTable rightsizing={rightsizing} nodes={nodes} recommendations={recommendations} />
      </Card>

      <Card>
        <RecommendationsList
          recommendations={recommendations}
          emptyMessage="No open rightsizing suggestions. Either everything looks fine, or there isn't enough history yet -- see the table above."
        />
      </Card>
    </div>
  );
}
