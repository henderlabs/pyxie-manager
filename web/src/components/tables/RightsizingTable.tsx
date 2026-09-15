"use client";

import type { Node, ObservationStats, Recommendation, RightsizingAssessment } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import ApplyRightsizingForm from "@/components/ApplyRightsizingForm";
import { formatBytes } from "@/lib/format";
import { Meter } from "@/components/Gauges";

// A stopped workload's p95/avg/max are whatever was observed the LAST time
// it was actually running -- real history, but not "as of right now", and
// can be arbitrarily old. A real gap found live: a VM stopped for months
// still showed a period-appropriate high memory p95 as if
// current, while the Workloads table's live meter correctly read 0% for
// the same VM -- the two tables were never disagreeing about the SAME
// number, they were answering different questions, but nothing here said
// so. Rendering this distinctly instead of a live-looking Meter bar makes
// that "this is history, not now" explicit instead of implied.
function StaleHistorical({ stats, status }: { stats: ObservationStats; status: string }) {
  if (stats.sample_count === 0) {
    return <span className="text-muted text-xs">no data while running</span>;
  }
  const asOf = stats.latest ? new Date(stats.latest).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : null;
  return (
    <span className="inline-flex items-center gap-1.5 text-muted text-xs whitespace-nowrap">
      <StatusBadge status={status} />
      <span>
        avg {stats.avg ?? "—"}% · max {stats.max ?? "—"}%{asOf && <> while last running (as of {asOf})</>}
      </span>
    </span>
  );
}

export default function RightsizingTable({
  rightsizing,
  nodes,
  recommendations,
}: {
  rightsizing: RightsizingAssessment[];
  nodes: Node[];
  recommendations: Recommendation[];
}) {
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const recommendationIdByWorkload = new Map(
    recommendations.filter((r) => r.object_id).map((r) => [r.object_id as string, r.id])
  );

  return (
    <Table
      rows={rightsizing.map((a) => ({ ...a, id: a.workload_id }))}
      emptyMessage="No workloads discovered yet."
      storageKey="operations-rightsizing"
      columns={[
        { header: "VMID", render: (a) => a.vmid, sortValue: (a) => a.vmid },
        { header: "Name", render: (a) => a.name || "—", sortValue: (a) => a.name },
        {
          header: "Host",
          render: (a) => nodeById.get(a.node_id)?.name || "—",
          sortValue: (a) => nodeById.get(a.node_id)?.name,
        },
        { header: "Observed", render: (a) => `${a.observation_days}d`, sortValue: (a) => a.observation_days, optional: true },
        {
          header: "Confidence",
          render: (a) => (
            <>
              <StatusBadge status={a.confidence === "insufficient_data" ? "warning" : a.confidence === "high" ? "healthy" : "unknown"} />
              <span className="ml-1 text-xs text-muted">{a.confidence.replace(/_/g, " ")}</span>
            </>
          ),
          sortValue: (a) => a.confidence,
          optional: true,
        },
        {
          header: "CPU (P95)",
          render: (a) =>
            a.currently_running ? (
              <div className="flex items-center gap-2">
                <Meter value={a.cpu.p95} width={56} />
                <span className="text-muted text-xs whitespace-nowrap">avg {a.cpu.avg ?? "—"}% · max {a.cpu.max ?? "—"}%</span>
              </div>
            ) : (
              <StaleHistorical stats={a.cpu} status={a.status} />
            ),
          sortValue: (a) => a.cpu.p95,
          optional: true,
        },
        {
          header: "Memory (P95)",
          render: (a) =>
            a.currently_running ? (
              <div className="flex items-center gap-2">
                <Meter value={a.memory.p95} width={56} />
                <span className="text-muted text-xs whitespace-nowrap">avg {a.memory.avg ?? "—"}% · max {a.memory.max ?? "—"}%</span>
              </div>
            ) : (
              <StaleHistorical stats={a.memory} status={a.status} />
            ),
          sortValue: (a) => a.memory.p95,
          optional: true,
        },
        {
          header: "Suggestion",
          tooltip: "Click the suggestion to preview and apply it directly -- same action as the card below.",
          render: (a) => {
            if (!a.cpu_suggestion && !a.memory_suggestion) {
              return (
                <span className="text-muted">
                  {a.current_vcpu ?? "—"} vCPU · {formatBytes(a.current_memory_bytes)}
                </span>
              );
            }
            return (
              <ApplyRightsizingForm
                workloadId={a.workload_id}
                workloadName={a.name || `VMID ${a.vmid}`}
                currentCores={a.current_vcpu}
                currentMemoryBytes={a.current_memory_bytes}
                suggestedCores={a.cpu_suggestion?.suggested ?? null}
                suggestedMemoryBytes={a.memory_suggestion?.suggested_bytes ?? null}
                recommendationId={recommendationIdByWorkload.get(a.workload_id)}
                triggerLabel={
                  <>
                    {a.cpu_suggestion && (
                      <div className={a.cpu_suggestion.direction === "increase" ? "text-bad" : "text-good"}>
                        {a.cpu_suggestion.current} → {a.cpu_suggestion.suggested} vCPU
                        {a.cpu_suggestion.direction === "increase" ? " (under-provisioned)" : ""}
                      </div>
                    )}
                    {a.memory_suggestion && (
                      <div className={a.memory_suggestion.direction === "increase" ? "text-bad" : "text-good"}>
                        {formatBytes(a.memory_suggestion.current_bytes)} → {formatBytes(a.memory_suggestion.suggested_bytes)}
                        {a.memory_suggestion.direction === "increase" ? " (under-provisioned)" : ""}
                      </div>
                    )}
                  </>
                }
              />
            );
          },
        },
      ]}
    />
  );
}
