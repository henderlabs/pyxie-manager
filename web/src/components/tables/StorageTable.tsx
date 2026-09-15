"use client";

import { useState } from "react";
import type { StorageItem } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import { formatBytes } from "@/lib/format";
import { Meter } from "@/components/Gauges";

export default function StorageTable({ storage }: { storage: StorageItem[] }) {
  // No longer visible to PVE -- kept in the DB rather than hard-deleted,
  // but hidden from the main list by default, same fix as the Workloads
  // and Hosts & Clusters pages' "Show removed" toggle (2026-09-15).
  const missingStorage = storage.filter((s) => s.is_missing);
  const [showRemoved, setShowRemoved] = useState(false);
  const visibleStorage = showRemoved ? storage : storage.filter((s) => !s.is_missing);

  return (
    <div>
      {missingStorage.length > 0 && (
        <label className="flex items-center gap-2 text-xs text-muted mb-2 cursor-pointer w-fit">
          <input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} />
          <span>
            Show removed ({missingStorage.length}){" "}
            <span className="text-text/70">-- no longer visible to PVE, kept here for history</span>
          </span>
        </label>
      )}
      <Table
      rows={visibleStorage}
      emptyMessage="No storage discovered yet."
      storageKey="infrastructure-storage"
      columns={[
        { header: "Name", render: (s) => s.name, sortValue: (s) => s.name },
        { header: "Type", render: (s) => s.type || "—", sortValue: (s) => s.type, optional: true },
        { header: "Scope", render: (s) => s.scope, sortValue: (s) => s.scope },
        {
          header: "Status",
          render: (s) => <StatusBadge status={s.is_missing ? "unknown" : s.status} />,
          sortValue: (s) => (s.is_missing ? "unknown" : s.status),
        },
        { header: "Capacity", render: (s) => formatBytes(s.capacity_bytes), sortValue: (s) => s.capacity_bytes, optional: true },
        { header: "Used", render: (s) => formatBytes(s.used_bytes), sortValue: (s) => s.used_bytes, optional: true },
        {
          header: "Utilization",
          render: (s) => (
            <Meter value={s.capacity_bytes && s.used_bytes ? (s.used_bytes / s.capacity_bytes) * 100 : null} width={80} />
          ),
          sortValue: (s) => (s.capacity_bytes && s.used_bytes ? (s.used_bytes / s.capacity_bytes) * 100 : null),
        },
      ]}
      />
    </div>
  );
}
