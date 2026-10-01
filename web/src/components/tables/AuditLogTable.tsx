"use client";

import type { AuditEvent } from "@/lib/api";
import { Table } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import { LogDetailGrid, prettyJson } from "@/components/LogDetail";
import OperationLinkViewer from "@/components/OperationLinkViewer";

export default function AuditLogTable({
  events,
  nodeNameById = {},
  workloadNameById = {},
  clusterNameById = {},
  siteNameById = {},
  providerNameById = {},
}: {
  events: AuditEvent[];
  /** Resolves node_id/workload_id/cluster_id/site_id/provider_id to the
   * name shown everywhere else in the app, same fix as the Policies page:
   * raw UUIDs aren't readable without cross-
   * referencing another page -- extended to every ID field in
   * this detail grid, not just the two that already had it. */
  nodeNameById?: Record<string, string>;
  workloadNameById?: Record<string, string>;
  clusterNameById?: Record<string, string>;
  siteNameById?: Record<string, string>;
  providerNameById?: Record<string, string>;
}) {
  return (
    <Table
      rows={events}
      emptyMessage="No audit events recorded yet."
      storageKey="platform-audit-log"
      columns={[
        { header: "Time", render: (e) => new Date(e.timestamp).toLocaleString(), sortValue: (e) => e.timestamp },
        { header: "Category", render: (e) => e.event_category, sortValue: (e) => e.event_category },
        { header: "Event", render: (e) => e.event_type, sortValue: (e) => e.event_type },
        {
          header: "Actor",
          render: (e) => `${e.actor || "—"} (${e.actor_type})`,
          sortValue: (e) => e.actor,
          optional: true,
        },
        {
          header: "Client IP",
          render: (e) => (typeof e.event_metadata?.client_ip === "string" ? e.event_metadata.client_ip : "—"),
          sortValue: (e) => (typeof e.event_metadata?.client_ip === "string" ? e.event_metadata.client_ip : ""),
          optional: true,
        },
        { header: "Result", render: (e) => <StatusBadge status={e.result} />, sortValue: (e) => e.result },
        { header: "Severity", render: (e) => <StatusBadge status={e.severity} />, sortValue: (e) => e.severity, optional: true },
        {
          header: "Details",
          // Only a handful of event types set event_metadata.summary today
          // (e.g. inventory.workload.missing) -- a plain string, not the
          // full envelope, since most rows have nothing worth surfacing
          // here and this column would otherwise be raw JSON noise.
          render: (e) => (typeof e.event_metadata?.summary === "string" ? e.event_metadata.summary : "—"),
          optional: true,
        },
      ]}
      renderDetail={(e) => {
        const { summary, operation_id, client_ip, ...restMetadata } = e.event_metadata || {};
        const metadataJson = prettyJson(restMetadata);
        return (
          <div>
            <LogDetailGrid
              fields={[
                ["Event ID", e.id],
                ["Client IP", typeof client_ip === "string" ? client_ip : null],
                ["Operation", e.operation],
                ["Node", (e.node_id && nodeNameById[e.node_id]) || e.node_id],
                ["Workload", (e.workload_id && workloadNameById[e.workload_id]) || e.workload_id],
                ["Cluster", (e.cluster_id && clusterNameById[e.cluster_id]) || e.cluster_id],
                ["Site", (e.site_id && siteNameById[e.site_id]) || e.site_id],
                ["Provider", (e.provider_id && providerNameById[e.provider_id]) || e.provider_id],
                ["Error", e.error],
                ["Metadata", metadataJson],
              ]}
            />
            {/* Every operation.* event carries its underlying Operation's
                id in metadata -- resolve it to the full record (target,
                actual PVE UPID, dry-run summary) instead of leaving it a
                raw id buried in the JSON above. */}
            {typeof operation_id === "string" && <OperationLinkViewer operationId={operation_id} />}
          </div>
        );
      }}
    />
  );
}
