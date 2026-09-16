import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrganizationOut(OrmModel):
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None


class SiteOut(OrmModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str


class SiteCreate(BaseModel):
    organization_id: uuid.UUID
    name: str
    slug: str


class SiteUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None


class ClusterOut(OrmModel):
    id: uuid.UUID
    site_id: uuid.UUID
    pve_target_id: uuid.UUID
    name: str
    quorate: Optional[bool]
    pve_version: Optional[str]
    node_count: int
    last_seen: datetime
    is_missing: bool


class NodeOut(OrmModel):
    id: uuid.UUID
    cluster_id: uuid.UUID
    site_id: uuid.UUID
    name: str
    status: str
    cpu_usage_pct: Optional[float]
    mem_usage_pct: Optional[float]
    mem_total_bytes: Optional[int]
    uptime_seconds: Optional[int]
    pve_version: Optional[str]
    kernel_version: Optional[str]
    pending_updates: Optional[int]
    maintenance_mode: bool = False
    maintenance_mode_since: Optional[datetime] = None
    maintenance_reason: Optional[str] = None
    maintenance_mode_by: Optional[str] = None
    last_seen: datetime
    is_missing: bool
    # Sum of running workloads' configured memory_bytes on this node --
    # attached by the router (not a real column, see inventory.py) so the
    # UI can warn when a node is running with configured VM memory close
    # to or past its physical total (PVE allows this deliberately, via
    # ballooning -- see NodeActionsForm.tsx / NodesTable.tsx's
    # "Overprovisioned" badge, surfaced on both the Maintenance and
    # Hosts & Clusters pages when a node runs that tight).
    allocated_memory_bytes: Optional[int] = None


class WorkloadOut(OrmModel):
    id: uuid.UUID
    node_id: uuid.UUID
    cluster_id: uuid.UUID
    vmid: int
    name: Optional[str]
    type: str
    status: str
    cpu_cores: Optional[int]
    memory_bytes: Optional[int]
    tags: Optional[list[str]] = None
    ha_state: Optional[str]
    sensitivity: str = "standard"
    downtime_tolerance: str = "standard"
    placement_notes: Optional[str] = None
    storage_preference: Optional[str] = None
    preferred_node_id: Optional[uuid.UUID] = None
    last_seen: datetime
    is_missing: bool


class StorageOut(OrmModel):
    id: uuid.UUID
    site_id: uuid.UUID
    cluster_id: Optional[uuid.UUID]
    node_id: Optional[uuid.UUID]
    name: str
    type: Optional[str]
    scope: str
    capacity_bytes: Optional[int]
    used_bytes: Optional[int]
    status: str
    last_seen: datetime
    is_missing: bool


class PveTaskOut(OrmModel):
    id: uuid.UUID
    cluster_id: uuid.UUID
    node_id: Optional[uuid.UUID]
    upid: str
    task_type: Optional[str]
    status: Optional[str]
    user: Optional[str]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]


class ProviderOut(OrmModel):
    id: uuid.UUID
    category_id: str
    provider_type: str
    name: str
    instance_name: str
    enabled: bool
    contract_version: int
    provider_version: Optional[str]
    connection_health: str
    last_success_at: Optional[datetime]
    last_error: Optional[str]
    capabilities: list[str] = []
    implementation_status: str
    live_validation_status: str


class PveTargetOut(OrmModel):
    id: uuid.UUID
    site_id: uuid.UUID
    provider_id: uuid.UUID
    name: str
    hostname: str
    api_port: int
    tls_verify: bool


class PveTargetCreate(BaseModel):
    site_id: uuid.UUID
    name: str
    hostname: str
    api_port: int = 8006
    tls_verify: bool = True
    token_user: str
    token_id: str
    token_secret: str


class PveTargetUpdate(BaseModel):
    name: str
    hostname: str
    api_port: int = 8006
    tls_verify: bool = True


class CredentialOut(OrmModel):
    id: uuid.UUID
    pve_target_id: uuid.UUID
    slot_name: str
    token_user: str
    token_id: str
    masked_secret: str = ""  # not a real ORM column -- filled in by the router after validation
    status: str
    last_validated_at: Optional[datetime]
    created_at: datetime


class CredentialCreate(BaseModel):
    slot_name: str  # inventory | maintenance | administrative
    token_user: str
    token_id: str
    token_secret: str


class CredentialUpdate(BaseModel):
    token_user: str
    token_id: str
    token_secret: str


class HostMaintenanceCredentialCreate(BaseModel):
    ssh_username: str
    ssh_private_key: str
    ssh_port: int = 22


class HostMaintenanceCredentialOut(OrmModel):
    id: uuid.UUID
    pve_target_id: uuid.UUID
    ssh_username: str
    ssh_port: int
    status: str
    last_validated_at: Optional[datetime]
    last_seen_wrapper_version: Optional[str]
    last_seen_contract_version: Optional[int]
    created_at: datetime


class AuditEventOut(OrmModel):
    id: uuid.UUID
    timestamp: datetime
    actor: Optional[str]
    actor_type: str
    site_id: Optional[uuid.UUID]
    cluster_id: Optional[uuid.UUID]
    node_id: Optional[uuid.UUID]
    workload_id: Optional[uuid.UUID]
    provider_id: Optional[uuid.UUID]
    event_category: str
    event_type: str
    operation: Optional[str]
    result: str
    severity: str
    error: Optional[str]
    event_metadata: Optional[dict[str, Any]] = None


class AppSettingsOut(OrmModel):
    inventory_refresh_interval_seconds: int
    tls_verify_default: bool
    timezone: str
    rightsizing_cpu_peak_target_pct: int
    rightsizing_mem_peak_target_pct: int
    rightsizing_round_vcpu_even: bool
    pve_mutations_enabled: bool


class AppSettingsUpdate(BaseModel):
    inventory_refresh_interval_seconds: Optional[int] = None
    tls_verify_default: Optional[bool] = None
    timezone: Optional[str] = None
    rightsizing_cpu_peak_target_pct: Optional[int] = None
    rightsizing_mem_peak_target_pct: Optional[int] = None
    rightsizing_round_vcpu_even: Optional[bool] = None
    pve_mutations_enabled: Optional[bool] = None
