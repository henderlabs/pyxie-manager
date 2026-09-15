"use client";

import { useEffect, useState } from "react";
import type { Node } from "@/lib/api";
import { Card, StatTile } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import { Meter } from "@/components/Gauges";
import { formatUptime } from "@/lib/format";
import { CpuIcon, MemoryIcon, ClockIcon, PackageIcon, HealthIcon, WrenchIcon } from "@/components/Icons";
import { onOperationsChanged } from "@/lib/operationsBus";

// Same live-poll pattern as the Nodes list / Dashboard -- this page was a
// one-time server snapshot, the one view never retrofitted when the
// others got 5s polling, confirmed live by watching a node's meters for
// several minutes post-exit-maintenance and seeing nothing move.
export default function NodeLiveStats({ nodeId, initialNode }: { nodeId: string; initialNode: Node }) {
  const [node, setNode] = useState<Node>(initialNode);

  useEffect(() => {
    function refresh() {
      fetch(`/api/nodes/${nodeId}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data: Node | null) => data && setNode(data))
        .catch(() => {});
    }
    const interval = setInterval(refresh, 5000);
    const unsubscribe = onOperationsChanged(refresh);
    return () => {
      clearInterval(interval);
      unsubscribe();
    };
  }, [nodeId]);

  return (
    <>
      {node.maintenance_mode && (
        <Card className="mb-4 border-warn/40">
          <div className="flex items-center gap-2 text-sm text-warn">
            <WrenchIcon className="w-4 h-4" />
            <span className="font-medium">
              In maintenance mode{node.maintenance_reason ? ` — ${node.maintenance_reason}` : ""}
              {node.maintenance_mode_since ? ` since ${new Date(node.maintenance_mode_since).toLocaleString()}` : ""}
              {node.maintenance_mode_by ? ` (by ${node.maintenance_mode_by})` : ""}
            </span>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 mb-4">
        <StatTile label="Status" value={<StatusBadge status={node.is_missing ? "unknown" : node.status} />} icon={<HealthIcon />} />
        <StatTile label="CPU" value={<Meter value={node.cpu_usage_pct} width={80} />} icon={<CpuIcon />} />
        <StatTile label="RAM" value={<Meter value={node.mem_usage_pct} width={80} />} icon={<MemoryIcon />} />
        <StatTile label="Uptime" value={formatUptime(node.uptime_seconds)} icon={<ClockIcon />} />
        <StatTile label="PVE Version" value={node.pve_version || "—"} icon={<PackageIcon />} />
      </div>
    </>
  );
}
