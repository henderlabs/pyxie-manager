import type { Finding } from "@/lib/api";

/** Every workload id a finding is actually "about" -- either directly
 * (object_type='workload') or indirectly via evidence.workload_ids (used by
 * e.g. affinity-rule violations, whose primary object is a node/cluster but
 * which are still about specific workloads). Used to badge a workload row
 * with any active finding that concerns it, wherever it's listed. */
export function findingWorkloadIds(f: Finding): string[] {
  const ids = new Set<string>();
  if (f.object_type === "workload" && f.object_id) ids.add(f.object_id);
  const evidence = f.evidence as { workload_ids?: unknown } | null;
  if (evidence && Array.isArray(evidence.workload_ids)) {
    for (const id of evidence.workload_ids) {
      if (typeof id === "string") ids.add(id);
    }
  }
  return Array.from(ids);
}
