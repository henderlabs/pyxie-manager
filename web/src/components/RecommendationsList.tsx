import type { Recommendation, RightsizingAssessment } from "@/lib/api";
import { EmptyState } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import RecommendationActions from "@/components/RecommendationActions";
import ApplyRightsizingForm from "@/components/ApplyRightsizingForm";
import ApplyPlacementForm from "@/components/ApplyPlacementForm";

export const CATEGORY_LABELS: Record<string, string> = {
  updates: "Updates",
  capacity: "Capacity",
  placement: "Load-Balancing",
  rightsizing: "Rightsizing",
};

// Shared between the Maintenance page (updates/capacity/placement -- the
// cluster/node-level categories) and the Rightsizing page (rightsizing
// only) so both stay visually and functionally identical to the one
// Recommendations page this was split out of.
export default function RecommendationsList({
  recommendations,
  rightsizingByWorkload = {},
  emptyMessage,
}: {
  recommendations: Recommendation[];
  rightsizingByWorkload?: Record<string, RightsizingAssessment>;
  emptyMessage: string;
}) {
  const byCategory: Record<string, Recommendation[]> = {};
  for (const r of recommendations) {
    (byCategory[r.category] ||= []).push(r);
  }
  const categories = Object.keys(byCategory);
  const seenApplyForWorkload = new Set<string>();

  if (categories.length === 0) {
    return <EmptyState message={emptyMessage} />;
  }

  return (
    <>
      {categories.map((category, i) => (
        <div key={category} className={`mb-5 last:mb-0 ${i > 0 ? "pt-5 border-t border-border" : ""}`}>
          <div className="text-xs font-bold uppercase tracking-wider text-proxmox mb-2">
            {CATEGORY_LABELS[category] || category}
          </div>
          <div className="divide-y divide-border">
            {byCategory[category].map((r) => (
              <div key={r.id} className={`py-3 ${r.lifecycle_state !== "open" ? "opacity-70" : ""}`}>
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="text-sm text-text font-medium">{r.title}</div>
                    {r.expected_benefit && <div className="text-xs text-muted mt-1">Benefit: {r.expected_benefit}</div>}
                    {r.possible_impact && <div className="text-xs text-muted">Impact: {r.possible_impact}</div>}
                    <div className="flex gap-2 mt-1.5 items-center flex-wrap">
                      <StatusBadge status={r.severity} />
                      <span className="text-xs text-muted">risk: {r.risk}</span>
                      {r.confidence && <span className="text-xs text-muted">confidence: {r.confidence}</span>}
                      {r.observation_window_days != null && (
                        <span className="text-xs text-muted">{r.observation_window_days}d observed</span>
                      )}
                      {r.lifecycle_state === "acknowledged" && r.acknowledged_at && (
                        <span className="text-xs text-muted">· acknowledged by {r.acknowledged_by ?? "unknown"}, {new Date(r.acknowledged_at).toLocaleString()}</span>
                      )}
                      {(r.lifecycle_state === "dismissed" || r.lifecycle_state === "snoozed") && (
                        <span className="text-xs text-muted">· dismissed{r.dismissed_by ? ` by ${r.dismissed_by}` : ""}{r.dismissed_at ? `, ${new Date(r.dismissed_at).toLocaleString()}` : ""}</span>
                      )}
                    </div>
                    {category === "rightsizing" &&
                      r.object_id &&
                      rightsizingByWorkload[r.object_id] &&
                      !seenApplyForWorkload.has(r.object_id) &&
                      (() => {
                        seenApplyForWorkload.add(r.object_id);
                        const a = rightsizingByWorkload[r.object_id];
                        return (
                          <ApplyRightsizingForm
                            workloadId={a.workload_id}
                            workloadName={a.name || `VMID ${a.vmid}`}
                            currentCores={a.current_vcpu}
                            currentMemoryBytes={a.current_memory_bytes}
                            suggestedCores={a.cpu_suggestion?.suggested ?? null}
                            suggestedMemoryBytes={a.memory_suggestion?.suggested_bytes ?? null}
                            recommendationId={r.id}
                          />
                        );
                      })()}
                    {category === "placement" &&
                      r.object_id &&
                      r.evidence &&
                      (() => {
                        const e = r.evidence as {
                          current_node?: string;
                          suggested_node?: string;
                          suggested_node_id?: string;
                          suggested_storage?: { id: string; name: string } | null;
                        };
                        if (!e.suggested_node_id) return null;
                        const workloadName = r.title.split(":")[0];
                        return (
                          <ApplyPlacementForm
                            workloadId={r.object_id!}
                            workloadName={workloadName}
                            currentNode={e.current_node || "?"}
                            destinationNodeId={e.suggested_node_id}
                            destinationNode={e.suggested_node || "?"}
                            destinationStorageId={e.suggested_storage?.id}
                            recommendationId={r.id}
                          />
                        );
                      })()}
                  </div>
                  <RecommendationActions id={r.id} state={r.lifecycle_state} />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}
