import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getSessionToken } from "./session";

const BASE_URL = process.env.API_INTERNAL_URL || "http://pyxie-manager-api:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Called only from Server Components / Route Handlers, never from the
// browser -- the browser never talks to the API directly. The session
// token lives in an HttpOnly cookie set by the /api/auth/* route handlers;
// this reads it via next/headers and forwards it as a Bearer token so
// every existing page's apiFetch call authenticates automatically.
// True when this call is part of a server-rendered PAGE (tagged by
// middleware), as opposed to a Route Handler serving a client-side fetch.
function inPageRender(): boolean {
  try {
    return headers().get("x-pyxie-page") === "1";
  } catch {
    return false;
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getSessionToken();
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
  });
  // A cookie the server no longer recognises (expired/revoked session, DB
  // restored, user deactivated): send the browser to sign in again instead of
  // showing the generic "Something went wrong" page. redirect() must stay
  // outside any try/catch -- it works by throwing.
  if (res.status === 401 && inPageRender()) redirect("/login");
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export type Cluster = {
  id: string;
  site_id: string;
  pve_target_id: string;
  name: string;
  quorate: boolean | null;
  pve_version: string | null;
  node_count: number;
  last_seen: string;
  is_missing: boolean;
};

export type Node = {
  id: string;
  cluster_id: string;
  site_id: string;
  name: string;
  status: string;
  cpu_usage_pct: number | null;
  mem_usage_pct: number | null;
  mem_total_bytes: number | null;
  uptime_seconds: number | null;
  pve_version: string | null;
  kernel_version: string | null;
  pending_updates: number | null;
  maintenance_mode: boolean;
  maintenance_mode_since: string | null;
  maintenance_reason: string | null;
  maintenance_mode_by: string | null;
  last_seen: string;
  is_missing: boolean;
  allocated_memory_bytes: number | null;
};

export type HostMaintenanceCredentialRecord = {
  id: string;
  pve_target_id: string;
  ssh_username: string;
  ssh_port: number;
  status: string;
  last_validated_at: string | null;
  last_seen_wrapper_version: string | null;
  last_seen_contract_version: number | null;
  created_at: string;
};

export type HostMaintenanceStatus = {
  node: string;
  credential_configured: boolean;
  management_ip_known: boolean;
  host_key_pinned: boolean;
  host_key_fingerprint: string | null;
  host_key_pinned_at: string | null;
  provisioned: boolean;
  reachable: boolean;
  wrapper_version?: string | null;
  contract_version?: number | null;
  contract_compatible?: boolean;
  capabilities?: string[];
  kernel_version?: string | null;
  upgradable_count?: number | null;
  reboot_required?: boolean | null;
  disk_free_bytes?: number | null;
  status_checked_at?: string | null;
  error?: string;
};

export type Workload = {
  id: string;
  node_id: string;
  cluster_id: string;
  vmid: number;
  name: string | null;
  type: string;
  status: string;
  cpu_cores: number | null;
  memory_bytes: number | null;
  tags: string[] | null;
  ha_state: string | null;
  sensitivity: string;
  downtime_tolerance: string;
  placement_notes: string | null;
  storage_preference: string | null;
  preferred_node_id: string | null;
  last_seen: string;
  is_missing: boolean;
};

export type StorageItem = {
  id: string;
  site_id: string;
  cluster_id: string | null;
  node_id: string | null;
  name: string;
  type: string | null;
  scope: string;
  capacity_bytes: number | null;
  used_bytes: number | null;
  status: string;
  last_seen: string;
  is_missing: boolean;
};

export type NetworkInterface = {
  iface: string;
  type: string;
  vlan_aware: boolean;
  allowed_vlans: string | null;
  ports: string | null;
  active: boolean;
  comments: string | null;
};

export type NodeNetworkTopology = {
  node: string;
  node_id: string;
  cluster_id: string;
  cluster_name: string;
  error?: string;
  interfaces: NetworkInterface[];
};

export type WorkloadNic = {
  net_id: string;
  bridge: string | null;
  vlan_tag: number | null;
  model: string | null;
  bridge_vlan_aware: boolean;
  allowed_vlans: string | null;
};

export type WorkloadNics = {
  workload_id: string;
  vmid: number;
  name: string | null;
  type: string;
  node: string;
  status: string;
  cluster_id: string;
  cluster_name: string;
  error?: string;
  nics: WorkloadNic[];
};

export type Site = { id: string; organization_id: string; name: string; slug: string };
export type Organization = { id: string; name: string; slug: string; created_at: string };

export type Provider = {
  id: string;
  category_id: string;
  provider_type: string;
  name: string;
  instance_name: string;
  enabled: boolean;
  contract_version: number;
  provider_version: string | null;
  connection_health: string;
  last_success_at: string | null;
  last_error: string | null;
  capabilities: string[];
  implementation_status: string;
  live_validation_status: string;
};

export type PveTarget = {
  id: string;
  site_id: string;
  provider_id: string;
  name: string;
  hostname: string;
  api_port: number;
  tls_verify: boolean;
};

export type Credential = {
  id: string;
  pve_target_id: string;
  slot_name: string;
  token_user: string;
  token_id: string;
  masked_secret: string;
  status: string;
  last_validated_at: string | null;
  created_at: string;
};

export type AuditEvent = {
  id: string;
  timestamp: string;
  actor: string | null;
  actor_type: string;
  site_id: string | null;
  cluster_id: string | null;
  node_id: string | null;
  workload_id: string | null;
  provider_id: string | null;
  event_category: string;
  event_type: string;
  operation: string | null;
  result: string;
  severity: string;
  error: string | null;
  event_metadata: Record<string, unknown> | null;
};

export type AppSettings = {
  inventory_refresh_interval_seconds: number;
  tls_verify_default: boolean;
  timezone: string;
  rightsizing_cpu_peak_target_pct: number;
  rightsizing_mem_peak_target_pct: number;
  rightsizing_round_vcpu_even: boolean;
  pve_mutations_enabled: boolean;
  smtp_enabled: boolean;
  smtp_host: string | null;
  smtp_port: number;
  smtp_username: string | null;
  smtp_from_address: string | null;
  smtp_use_tls: boolean;
  smtp_password_set: boolean;
  notification_recipient: string | null;
  notification_hold_down_minutes: number;
};

export type DashboardSummary = {
  counts: { clusters: number; nodes: number; vms: number; containers: number; storage: number };
  cluster_status: string;
  nodes_online: number;
  nodes_total: number;
  workloads_running: number;
  workloads_total: number;
  storage_used_pct: number | null;
  nodes_with_updates: number;
  nodes_in_maintenance: number;
  operations_awaiting_approval: number;
  failed_tasks: number;
  nodes_detail: Array<{
    id: string;
    name: string;
    status: string;
    cpu_usage_pct: number | null;
    mem_usage_pct: number | null;
    pending_updates: number | null;
    maintenance_mode: boolean;
  }>;
};


export type ClusterLogEntry = {
  id: string;
  cluster_id: string;
  node: string | null;
  tag: string | null;
  priority: number | null;
  message: string | null;
  logged_at: string | null;
};

export type PveTask = {
  id: string;
  cluster_id: string;
  node_id: string | null;
  upid: string;
  task_type: string | null;
  status: string | null;
  exit_status: string | null;
  user: string | null;
  started_at: string | null;
  ended_at: string | null;
  vmid: number | null;
  workload_name: string | null;
  operation_id: string | null;
  operation_type_id: string | null;
  initiated_by: string | null;
};

export type Finding = {
  id: string;
  object_type: string | null;
  object_id: string | null;
  category: string;
  severity: "critical" | "warning" | "info" | "unknown";
  title: string;
  evidence: Record<string, unknown> | null;
  first_observed: string;
  last_observed: string;
  active: boolean;
  resolved_at: string | null;
  confidence: string | null;
};

export type Recommendation = {
  id: string;
  object_type: string | null;
  object_id: string | null;
  category: string;
  title: string;
  evidence: Record<string, unknown> | null;
  expected_benefit: string | null;
  possible_impact: string | null;
  severity: string;
  risk: string;
  confidence: string | null;
  observation_window_days: number | null;
  generated_at: string;
  lifecycle_state: string;
  snoozed_until: string | null;
};

export type ObservationStats = {
  avg: number | null;
  p95: number | null;
  max: number | null;
  sample_count: number;
  earliest: string | null;
  latest: string | null;
};

export type RightsizingAssessment = {
  workload_id: string;
  node_id: string;
  vmid: number;
  name: string | null;
  current_vcpu: number | null;
  current_memory_bytes: number | null;
  cpu: ObservationStats;
  memory: ObservationStats;
  observation_days: number;
  confidence: string;
  currently_running: boolean;
  status: string;
  cpu_suggestion: { current: number; suggested: number; direction: "increase" | "decrease"; reason: string } | null;
  memory_suggestion: {
    current_bytes: number;
    suggested_bytes: number;
    direction: "increase" | "decrease";
    reason: string;
  } | null;
};

export type NodeCapacity = {
  node_id: string;
  name: string;
  maintenance_mode: boolean;
  allocated_vcpu: number;
  allocated_memory_bytes: number;
  node_memory_total_bytes: number | null;
  workload_count: number;
  observed_cpu_pct: number | null;
  observed_cpu_p95_pct: number | null;
  observed_mem_pct: number | null;
  observed_mem_p95_pct: number | null;
  memory_headroom_bytes: number | null;
};

export type ClusterCapacity = {
  cluster_id: string;
  name: string;
  nodes: NodeCapacity[];
  total_memory_bytes: number;
  total_allocated_memory_bytes: number;
  memory_allocation_pct: number | null;
  busiest_node: string | null;
  most_constrained_resource: string | null;
  workloads_contributing_most_pressure: Array<{ vmid: number; name: string | null; cpu_pressure_score: number }>;
};

export type ProtectionResultRow = {
  workload_id: string;
  vmid: number | null;
  name: string | null;
  provider_id: string;
  protected: "true" | "false" | "unknown";
  last_successful_job_at: string | null;
  last_restore_point_at: string | null;
  sla_defined: boolean | null;
  sla_compliant: string;
  restore_verified: string;
  confidence: string | null;
  last_error: string | null;
};

export type BackupJob = {
  job_id: string;
  cluster_id: string;
  cluster_name: string;
  storage: string;
  schedule: string | null;
  enabled: boolean;
  all_guests: boolean;
  exclude_vmids: number[];
  vmid_list: number[];
  members: { workload_id: string; vmid: number; name: string | null; included: boolean }[];
  unknown_vmids: number[];
};

export type PolicyRow = {
  id: string;
  scope_type: string;
  scope_id: string | null;
  key: string;
  value: unknown;
  updated_at: string;
};

export type Operation = {
  id: string;
  operation_type_id: string;
  correlation_id: string;
  plan_id: string | null;
  parent_operation_id: string | null;
  cluster_id: string | null;
  node_id: string | null;
  workload_id: string | null;
  status: string;
  stage: string | null;
  stages_completed: string[];
  stages_pending: string[];
  context: Record<string, unknown> | null;
  // Shape varies by operation_type_id -- vm.live_migrate has source_node/
  // target_node/vmid/workload_name/target_storage; W2-W6 types have their
  // own fields (node, plan, migrate_plan, update_count, etc). Every type
  // always has eligible/reasons/blocking_safety_rules.
  dry_run_result: {
    eligible: boolean; reasons: string[]; blocking_safety_rules: string[];
    source_node?: string; target_node?: string; target_storage?: string | null;
    vmid?: number; workload_name?: string | null; node?: string;
    [key: string]: unknown;
  } | null;
  progress: { transferred_bytes: number | null; total_bytes: number | null; rate_bytes_per_sec: number | null; pct: number | null; raw_line: string } | null;
  pve_upid: string | null;
  verification_result: Record<string, unknown> | null;
  rollback_classification: string | null;
  blocking_safety_rules: string[] | null;
  error: string | null;
  created_by: string | null;
  approved_by: string | null;
  dismissed: boolean;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type InternalJobRunRow = {
  id: string;
  job_name: string;
  started_at: string;
  ended_at: string | null;
  status: string;
  result_summary: unknown;
  error: string | null;
};

export type Me = { email: string; display_name: string | null; is_admin: boolean };

export type UserAccount = {
  id: string;
  email: string;
  display_name: string | null;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  pending_invite: boolean;
};

export type NotificationRule = {
  id: string;
  name: string;
  enabled: boolean;
  categories: string[];
  min_severity: "warning" | "critical";
  send_recovery: boolean;
  recipients: string[];
  include_admins: boolean;
};

export type NotificationCatalog = {
  categories: { key: string; label: string; description: string }[];
  severities: string[];
};

export type NotificationItem = {
  id: string;
  severity: string;
  title: string;
  message: string | null;
  status: string;
  source: string;
  created_at: string;
};
