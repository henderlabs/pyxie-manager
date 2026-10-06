import Link from "next/link";
import { Suspense } from "react";
import { notFound } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import type { Node, StorageItem } from "@/lib/api";
import WorkloadDetail from "@/components/WorkloadDetail";
import type { WorkloadOverview, WorkloadTask } from "@/lib/workloadDetail";

export default async function WorkloadDetailPage({ params }: { params: { id: string } }) {
  const [overview, tasks, nodes, storage] = await Promise.all([
    apiFetch<WorkloadOverview>(`/api/workloads/${params.id}/overview`).catch((e) => {
      if (e instanceof ApiError && (e.status === 404 || e.status === 422)) notFound();
      throw e;
    }),
    apiFetch<WorkloadTask[]>(`/api/workloads/${params.id}/tasks`),
    apiFetch<Node[]>("/api/nodes"),
    apiFetch<StorageItem[]>("/api/storage"),
  ]);
  return (
    <div>
      <div className="text-sm text-muted mb-3">
        <Link href="/infrastructure/workloads" className="text-accent hover:underline">Workloads</Link> / {overview.workload.name || overview.workload.vmid}
      </div>
      <Suspense fallback={null}>
        <WorkloadDetail overview={overview} tasks={tasks} nodes={nodes} storage={storage} />
      </Suspense>
    </div>
  );
}
