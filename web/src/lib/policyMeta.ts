// Human-readable descriptions for known policy keys, so the Policies page
// (Platform > Policies) can show a labeled, typed control with an
// explanation instead of a raw JSON blob -- most policy values are a
// {field: scalar} object (the 4 org-wide defaults seeded in seed.py), but
// the placement.* node/cluster-scoped ones store the scalar directly
// (see placement.py) -- `field: null` means "the value itself is the
// scalar," not wrapped in an object.
//
// Any key not listed here falls back to the raw JSON editor (still needed
// -- this page is deliberately an escape hatch for anything not covered by
// a dedicated settings UI elsewhere, so it can't hardcode every future
// key).

export type PolicyControl = "percent" | "count-hours" | "count-days" | "boolean" | "tier" | "text" | "storage-ref";

export type PolicyMeta = {
  label: string;
  description: string;
  field: string | null;
  control: PolicyControl;
};

export const POLICY_META: Record<string, PolicyMeta> = {
  "storage.warning_threshold_pct": {
    label: "Storage warning threshold",
    description: "A storage pool is flagged as a finding once it's this full.",
    field: "pct",
    control: "percent",
  },
  "protection.max_backup_age_hours": {
    label: "Max backup age",
    description: "How long since a workload's last successful backup before it's flagged as stale.",
    field: "hours",
    control: "count-hours",
  },
  "protection.require_for_all_workloads": {
    label: "Require backup coverage for every workload",
    description: "When on, a workload with no backup coverage at all counts as non-compliant, not just one with a stale backup.",
    field: "enabled",
    control: "boolean",
  },
  "metrics.retention_days": {
    label: "Metrics retention",
    description: "How long historical CPU/RAM/disk metrics are kept before being pruned.",
    field: "days",
    control: "count-days",
  },
  "placement.performance_tier": {
    label: "Performance tier",
    description: "This node's relative performance for placement scoring -- higher tiers are preferred destinations. Usually set from the Hosts & Clusters page, not here.",
    field: null,
    control: "tier",
  },
  "placement.trust_tier": {
    label: "Trust tier",
    description: "This node's trust level -- workloads tagged sensitivity=\"restricted\" may be blocked from landing on a low-trust node. Usually set from the Hosts & Clusters page, not here.",
    field: null,
    control: "tier",
  },
  "placement.storage_preference": {
    label: "Storage preference",
    description: "Default storage-locality preference (local vs. shared) for workloads in this cluster that haven't set their own.",
    field: null,
    control: "text",
  },
  "placement.default_storage_id": {
    label: "Default storage",
    description: "Storage pool used by default for this node's migrations, when a workload calls for local storage. A reference to a specific storage pool -- shown by name, read-only here; change it from the Hosts & Clusters page.",
    field: null,
    control: "storage-ref",
  },
  "placement.notes": {
    label: "Notes",
    description: "Free-text notes about why this node's placement profile is set the way it is -- PyXie-only, never synced to/from PVE.",
    field: null,
    control: "text",
  },
};
