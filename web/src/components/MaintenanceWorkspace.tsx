"use client";

import { useEffect, useState } from "react";
import type { HostMaintenanceStatus, Node, StorageItem, Workload } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import NodeActionsForm from "@/components/NodeActionsForm";
import WorkloadLifecycleForm from "@/components/WorkloadLifecycleForm";
import { onOperationsChanged } from "@/lib/operationsBus";
import { WrenchIcon, ClockIcon } from "@/components/Icons";

type WorkloadMetric = { cpu_pct?: number; mem_pct?: number };
type WorkloadStorage = { name: string; scope: string | null };

export default function MaintenanceWorkspace({
  nodes: initialNodes,
  workloads,
  workloadMetrics,
  workloadStorage,
  storage,
  defaultStorageByNode,
  initialNodeId,
  initialActionKey,
  initialWorkloadId,
}: {
  nodes: Node[];
  workloads: Workload[];
  workloadMetrics: Record<string, WorkloadMetric>;
  workloadStorage: Record<string, WorkloadStorage>;
  storage: StorageItem[];
  defaultStorageByNode: Record<string, string | null>;
  initialNodeId?: string;
  initialActionKey?: string;
  initialWorkloadId?: string;
}) {
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>(initialNodeId ? [initialNodeId] : []);
  const [nodes, setNodes] = useState<Node[]>(initialNodes);
  // Starts empty on purpose -- the page no longer waits on this. Measured
  // directly (2026-09-12): host-maintenance-status (the SSH probe behind
  // Reboot Required) takes 1-3+ seconds PER NODE. It used to be fetched
  // server-side before either the Nodes or Maintenance page would render
  // at all, which acted like it was pulling every node before it pulled
  // up the page. Now the page shell (nodes,
  // badges, meters -- all cheap DB reads) paints immediately, and this
  // fills in a moment later. The one owner of this state lives here (not
  // duplicated inside NodeActionsForm) so it's fetched once, not twice.
  const [hostMaintenance, setHostMaintenance] = useState<Record<string, HostMaintenanceStatus | null>>({});

  useEffect(() => {
    function refreshNodes() {
      fetch("/api/nodes")
        .then((r) => (r.ok ? r.json() : null))
        .then((data: Node[] | null) => data && setNodes(data))
        .catch(() => {});
    }
    function refreshHostMaintenance() {
      Promise.all(
        initialNodes.map(async (n) => {
          try {
            const res = await fetch(`/api/nodes/${n.id}/host-maintenance-status`);
            return [n.id, res.ok ? ((await res.json()) as HostMaintenanceStatus) : null] as const;
          } catch {
            return [n.id, null] as const;
          }
        })
      ).then((entries) => setHostMaintenance(Object.fromEntries(entries)));
    }
    refreshNodes();
    refreshHostMaintenance();
    const interval = setInterval(refreshNodes, 5000);
    // host-maintenance-status never had its own heartbeat -- only the bus
    // (fired by a user action elsewhere on the page) refreshed it, so a
    // reboot that finished while nobody clicked anything here left "Reboot
    // Required" showing stale until a manual page reload -- a real gap
    // found live, a Full Maintenance run rebooted and came back clean but
    // the badge didn't clear until a manual refresh. Slower than the
    // cheap node poll above since this is a real SSH probe per node
    // (1-3+s each), not a DB read.
    const hostMaintenanceInterval = setInterval(refreshHostMaintenance, 30000);
    const unsubscribe = onOperationsChanged(() => {
      refreshNodes();
      refreshHostMaintenance();
    });
    return () => {
      clearInterval(interval);
      clearInterval(hostMaintenanceInterval);
      unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const inMaintenance = nodes.filter((n) => n.maintenance_mode);
  const needsReboot = nodes.filter((n) => hostMaintenance[n.id]?.reboot_required);

  return (
    <>
      {(inMaintenance.length > 0 || needsReboot.length > 0) && (
        <div className="flex flex-wrap gap-3 mb-4">
          {inMaintenance.length > 0 && (
            <Card className="border-warn/40 flex-1 min-w-[280px]">
              <div className="flex items-center gap-2 text-sm text-warn">
                <WrenchIcon className="w-4 h-4" />
                <span className="font-medium">
                  {inMaintenance.map((n) => n.name).join(", ")} {inMaintenance.length === 1 ? "is" : "are"} in maintenance mode
                </span>
              </div>
            </Card>
          )}
          {needsReboot.length > 0 && (
            <Card className="border-bad/40 flex-1 min-w-[280px]">
              <div className="flex items-center gap-2 text-sm text-bad">
                <ClockIcon className="w-4 h-4" />
                <span className="font-medium">
                  {needsReboot.map((n) => n.name).join(", ")} {needsReboot.length === 1 ? "needs" : "need"} a reboot
                </span>
              </div>
            </Card>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <Card>
          <CardTitle>Nodes</CardTitle>
          <NodeActionsForm
            nodes={nodes}
            hostMaintenanceByNode={hostMaintenance}
            storage={storage}
            defaultStorageByNode={defaultStorageByNode}
            initialNodeId={initialNodeId}
            initialActionKey={initialActionKey}
            onSelectionChange={setSelectedNodeIds}
          />
        </Card>
        <Card>
          <CardTitle>Workloads</CardTitle>
          <WorkloadLifecycleForm
            workloads={workloads}
            nodes={nodes}
            storage={storage}
            metrics={workloadMetrics}
            currentStorageByWorkload={workloadStorage}
            selectedNodeIds={selectedNodeIds}
            initialWorkloadId={initialWorkloadId}
          />
        </Card>
      </div>
    </>
  );
}
