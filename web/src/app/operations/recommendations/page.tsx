import { apiFetch } from "@/lib/api";
import type { Node, Recommendation, RightsizingAssessment } from "@/lib/api";
import { PageHeader } from "@/components/Card";
import RightsizingView, { type RightsizingTab } from "@/components/RightsizingView";
import { LightbulbIcon } from "@/components/Icons";

// Narrowed to VM/CT right-sizing only -- the cluster/node-level categories (updates, capacity, load-balancing) live on
// the Maintenance page's own Recommendations column.
//
// Table first, details second: the table is the primary scan-and-act surface -- its Suggestion column IS the apply
// action. The cards below stay for full context (benefit/impact/severity). Acknowledge / Dismiss work like Health;
// tabs All (default) / Open / Acknowledged / Dismissed / Resolved.
export default async function RecommendationsPage({ searchParams }: { searchParams: { view?: string } }) {
  const tab: RightsizingTab = (["all", "open", "acknowledged", "dismissed", "resolved"] as const).find((v) => v === searchParams.view) ?? "all";
  const [recommendations, resolved, rightsizing, nodes, status] = await Promise.all([
    apiFetch<Recommendation[]>("/api/recommendations?category=rightsizing&status=any"),
    tab === "resolved" ? apiFetch<Recommendation[]>("/api/recommendations?category=rightsizing&status=resolved") : Promise.resolve([] as Recommendation[]),
    apiFetch<RightsizingAssessment[]>("/api/rightsizing"),
    apiFetch<Node[]>("/api/nodes"),
    apiFetch<{ computed_at: string | null }>("/api/rightsizing/status"),
  ]);
  const count = (s: (r: Recommendation) => boolean) => recommendations.filter(s).length;
  const chips: { key: RightsizingTab; label: string }[] = [
    { key: "all", label: `All (${recommendations.length})` },
    { key: "open", label: `Open (${count((r) => r.lifecycle_state === "open")})` },
    { key: "acknowledged", label: `Acknowledged (${count((r) => r.lifecycle_state === "acknowledged")})` },
    { key: "dismissed", label: `Dismissed (${count((r) => r.lifecycle_state === "dismissed" || r.lifecycle_state === "snoozed")})` },
    { key: "resolved", label: "Resolved" },
  ];

  return (
    <div>
      <PageHeader
        title="Rightsizing (Historical Data)"
        subtitle="VM/CT sizing suggestions from observed usage -- explainable, evidence-backed, nothing here executes automatically"
        icon={<LightbulbIcon className="w-5 h-5" />}
      />
      <p className="text-xs text-muted -mt-4 mb-5">
        Historical analysis -- CPU/RAM observed over days to weeks, refreshed automatically every few minutes in the
        background (or on demand below). Not live like the Workloads page&apos;s usage meters; see there for
        real-time CPU/RAM.
      </p>
      <div className="flex gap-2 mb-4 text-sm flex-wrap">
        {chips.map((c) => (
          <a key={c.key} href={c.key === "all" ? "/operations/recommendations" : `/operations/recommendations?view=${c.key}`}
            className={`px-3 py-1 rounded border ${tab === c.key ? "border-accent text-accent bg-accent/10" : "border-border text-muted"}`}>
            {c.label}
          </a>
        ))}
      </div>
      <RightsizingView tab={tab} rightsizing={rightsizing} nodes={nodes} recommendations={recommendations} resolved={resolved} computedAt={status.computed_at} />
    </div>
  );
}
