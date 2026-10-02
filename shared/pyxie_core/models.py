import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    BigInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from .db import Base


def uuid_pk():
    return Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def now_utc():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Core hierarchy: Organization -> Site -> Cluster -> Node -> Workload
# ---------------------------------------------------------------------------


class Organization(Base):
    __tablename__ = "organizations"

    id = uuid_pk()
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)

    sites = relationship("Site", back_populates="organization")


class Site(Base):
    __tablename__ = "sites"

    id = uuid_pk()
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)

    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_site_org_slug"),)

    organization = relationship("Organization", back_populates="sites")
    clusters = relationship("Cluster", back_populates="site")
    pve_targets = relationship("PveTarget", back_populates="site")


class Cluster(Base):
    __tablename__ = "clusters"

    id = uuid_pk()
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)
    pve_target_id = Column(UUID(as_uuid=True), ForeignKey("pve_targets.id"), nullable=False)
    name = Column(String, nullable=False)
    quorate = Column(Boolean, nullable=True)
    pve_version = Column(String, nullable=True)
    node_count = Column(Integer, nullable=False, default=0)
    first_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    is_missing = Column(Boolean, nullable=False, default=False)

    site = relationship("Site", back_populates="clusters")
    nodes = relationship("Node", back_populates="cluster")
    workloads = relationship("Workload", back_populates="cluster")


class Node(Base):
    __tablename__ = "nodes"

    id = uuid_pk()
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id"), nullable=False)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="unknown")  # online/offline/unknown
    management_ip = Column(String, nullable=True)
    # Whether PyXie may use this member as a PVE API endpoint (failover
    # selection, set on Settings > Integrations). Discovery never changes it.
    failover_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    # Pinned SSH host key for Stage W4 (host-maintenance SSH), deliberately
    # separate from anything PVE-API-related -- set only via an explicit
    # operator-confirmed pin action (see credentials.py /
    # host_maintenance_client.py), never auto-trusted on first connect.
    ssh_host_key_type = Column(String, nullable=True)
    ssh_host_key_base64 = Column(Text, nullable=True)
    ssh_host_key_fingerprint = Column(String, nullable=True)
    ssh_host_key_pinned_at = Column(DateTime(timezone=True), nullable=True)
    ssh_host_key_pinned_by = Column(String, nullable=True)
    cpu_usage_pct = Column(Float, nullable=True)
    mem_usage_pct = Column(Float, nullable=True)
    mem_total_bytes = Column(BigInteger, nullable=True)
    uptime_seconds = Column(BigInteger, nullable=True)
    pve_version = Column(String, nullable=True)
    kernel_version = Column(String, nullable=True)
    pending_updates = Column(Integer, nullable=True)
    # Explicit maintenance-mode state, independent of any specific job --
    # host.update/host.reboot both refuse to run unless this is true. Entered
    # via node.enter_maintenance (evacuates, then sets this) and exited via
    # node.exit_maintenance (restarts anything shut down for entry, then
    # clears this) -- either standalone, or implicitly around the patch/
    # reboot steps of a maintenance.run. Not tied to why -- physical
    # hardware work and a PyXie-driven patch cycle both just need this true.
    maintenance_mode = Column(Boolean, nullable=False, default=False)
    maintenance_mode_since = Column(DateTime(timezone=True), nullable=True)
    maintenance_reason = Column(Text, nullable=True)
    maintenance_mode_by = Column(String, nullable=True)
    first_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    is_missing = Column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("cluster_id", "name", name="uq_node_cluster_name"),)

    cluster = relationship("Cluster", back_populates="nodes")
    # Two FKs from Workload now point at nodes.id (node_id, preferred_node_id)
    # -- foreign_keys disambiguates which one this relationship follows.
    workloads = relationship("Workload", back_populates="node", foreign_keys="Workload.node_id")


class Workload(Base):
    __tablename__ = "workloads"

    id = uuid_pk()
    node_id = Column(UUID(as_uuid=True), ForeignKey("nodes.id"), nullable=False)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id"), nullable=False)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)
    vmid = Column(Integer, nullable=False)
    name = Column(String, nullable=True)
    type = Column(String, nullable=False)  # vm | lxc
    status = Column(String, nullable=False, default="unknown")
    cpu_cores = Column(Integer, nullable=True)
    memory_bytes = Column(BigInteger, nullable=True)
    # See migration 0035. True: PVE reports guest-used memory; False: only the host-side
    # figure (mem == memhost in every recent sample); None: not evaluated / n/a.
    mem_guest_stats = Column(Boolean, nullable=True)
    # Host-side size of the VM process (PVE `memhost`), from the RRD series each cycle.
    mem_host_bytes = Column(BigInteger, nullable=True)
    # Live memory in use as PVE reports it (VM list `mem`), refreshed each inventory
    # cycle; what the PVE summary screen shows. None when not running / not seen yet.
    mem_used_bytes = Column(BigInteger, nullable=True)
    os_type = Column(String, nullable=True)  # PVE qemu config 'ostype' (win11, win10, l26, other, ...); null for lxc/unset
    tags = Column(JSONB, nullable=True)
    ha_state = Column(String, nullable=True)
    sensitivity = Column(String, nullable=False, default="standard")  # standard | restricted -- restricted may not land on a trust_tier=low node
    downtime_tolerance = Column(String, nullable=False, default="standard")  # low | standard | high -- 'low' means CANNOT tolerate downtime (critical); also may not land on trust_tier=low
    placement_notes = Column(Text, nullable=True)  # PyXie-only free text -- why this profile was set, never synced from/to PVE
    # Sticky per-VM storage preference for migration/placement destination
    # selection: 'local' | 'shared' | null. Null means "infer from wherever
    # its disk lives today" (already-shared stays shared; already-local
    # targets the destination node's own local storage) -- an explicit
    # value overrides that inference, e.g. a VM the operator wants moved
    # onto shared storage going forward despite currently being node-local.
    storage_preference = Column(String, nullable=True)
    # Sticky per-VM preferred HOST (node) to live on -- a soft placement
    # preference, not a hard pin: recommend_destinations() gives this node
    # a strong scoring bonus so it wins whenever it's otherwise eligible,
    # but every real hard block (headroom, CPU compat, passthrough, trust
    # tier, PyXie affinity) still overrides it, same philosophy as every
    # other judgment-call check. Null means no preference -- balance/tier
    # scoring alone decides, as today. Deliberately its own column, not
    # an affinity rule
    # (those express workload<->workload/tag relationships, not
    # workload<->node) and not a node tier (those classify a node in
    # general, they don't let a workload point at one).
    preferred_node_id = Column(UUID(as_uuid=True), ForeignKey("nodes.id"), nullable=True)
    first_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    is_missing = Column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("cluster_id", "vmid", name="uq_workload_cluster_vmid"),)

    node = relationship("Node", back_populates="workloads", foreign_keys=[node_id])
    preferred_node = relationship("Node", foreign_keys=[preferred_node_id])
    cluster = relationship("Cluster", back_populates="workloads")


class Storage(Base):
    __tablename__ = "storage"

    id = uuid_pk()
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id"), nullable=True)
    node_id = Column(UUID(as_uuid=True), ForeignKey("nodes.id"), nullable=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=True)  # pve storage plugin type: dir, lvm, zfs, nfs, ...
    scope = Column(String, nullable=False)  # node-local | cluster-shared | external | replicated
    capacity_bytes = Column(BigInteger, nullable=True)
    used_bytes = Column(BigInteger, nullable=True)
    status = Column(String, nullable=False, default="unknown")
    first_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    is_missing = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("site_id", "node_id", "cluster_id", "name", name="uq_storage_identity"),
    )


class PveTask(Base):
    __tablename__ = "pve_tasks"

    id = uuid_pk()
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id"), nullable=False)
    node_id = Column(UUID(as_uuid=True), ForeignKey("nodes.id"), nullable=True)
    upid = Column(String, nullable=False, unique=True)
    task_type = Column(String, nullable=True)
    status = Column(String, nullable=True)
    user = Column(String, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    exit_status = Column(String, nullable=True)
    first_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)


class ClusterLogEntry(Base):
    """PVE's own syslog-style cluster log (see PveClient.cluster_log()) --
    ambient system activity (daemon restarts, corosync/quorum events,
    hardware issues), distinct from PveTask (a job's outcome). PVE itself
    only keeps a small rolling buffer, so this persists what's been seen
    to build real history, deduped on (cluster_id, pve_id) -- pve_id is
    PVE's own stable identifier for that log line, not something PyXie
    invents."""

    __tablename__ = "cluster_log_entries"

    id = uuid_pk()
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id"), nullable=False)
    pve_id = Column(String, nullable=False)
    node = Column(String, nullable=True)
    tag = Column(String, nullable=True)
    priority = Column(Integer, nullable=True)  # syslog pri: 0=emerg .. 7=debug, lower is more severe
    message = Column(Text, nullable=True)
    logged_at = Column(DateTime(timezone=True), nullable=True)
    first_seen = Column(DateTime(timezone=True), default=now_utc, nullable=False)

    __table_args__ = (UniqueConstraint("cluster_id", "pve_id", name="uq_cluster_log_cluster_pve_id"),)


# ---------------------------------------------------------------------------
# Provider framework: categories + capabilities are namespaced data, not enums
# ---------------------------------------------------------------------------


class ProviderCategory(Base):
    __tablename__ = "provider_categories"

    id = Column(String, primary_key=True)  # e.g. 'pve', 'protection', 'monitoring'
    description = Column(String, nullable=True)


class Capability(Base):
    __tablename__ = "capabilities"

    id = Column(String, primary_key=True)  # e.g. 'pve.inventory.read'
    category_id = Column(String, ForeignKey("provider_categories.id"), nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)


class Provider(Base):
    """A configured provider instance (e.g. one PVE cluster connection)."""

    __tablename__ = "providers"

    id = uuid_pk()
    category_id = Column(String, ForeignKey("provider_categories.id"), nullable=False)
    provider_type = Column(String, nullable=False)  # 'pve', 'pbs', 'veeam', 'commvault', ...
    name = Column(String, nullable=False)
    instance_name = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    contract_version = Column(Integer, nullable=False, default=1)
    provider_version = Column(String, nullable=True)
    configuration = Column(JSONB, nullable=True)
    connection_health = Column(String, nullable=False, default="unknown")
    # connected | connection_failed | authentication_failed | tls_error | unavailable | unknown
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    # Structural/implementation maturity -- independent of runtime connection_health.
    # not_implemented | implemented | contract_tested | live_tested | production_validated
    implementation_status = Column(String, nullable=False, default="not_implemented")
    live_validation_status = Column(String, nullable=False, default="not_tested")  # not_tested | tested
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class ProviderCapability(Base):
    """Declares which capabilities a given provider instance supports."""

    __tablename__ = "provider_capabilities"

    provider_id = Column(UUID(as_uuid=True), ForeignKey("providers.id"), primary_key=True)
    capability_id = Column(String, ForeignKey("capabilities.id"), primary_key=True)


class CapabilityGrant(Base):
    """Authorizes a provider instance to exercise a capability within a scope.

    scope_id is nullable to represent a broad/default grant; Phase 0 only ever
    creates broad grants, but the scope_type vocabulary already anticipates
    organization/site/cluster/node/workload scoping without a schema change.
    """

    __tablename__ = "capability_grants"

    id = uuid_pk()
    provider_id = Column(UUID(as_uuid=True), ForeignKey("providers.id"), nullable=False)
    capability_id = Column(String, ForeignKey("capabilities.id"), nullable=False)
    scope_type = Column(String, nullable=False, default="global")
    # global | organization | site | cluster | node | workload
    scope_id = Column(UUID(as_uuid=True), nullable=True)
    granted_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    granted_by = Column(String, nullable=True)


# ---------------------------------------------------------------------------
# PVE targets + credentials
# ---------------------------------------------------------------------------


class PveTarget(Base):
    __tablename__ = "pve_targets"

    id = uuid_pk()
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("providers.id"), nullable=False)
    name = Column(String, nullable=False)
    hostname = Column(String, nullable=False)
    api_port = Column(Integer, nullable=False, default=8006)
    tls_verify = Column(Boolean, nullable=False, default=True)
    tls_fingerprint = Column(String, nullable=True)
    # Written by every discovery run: {active, preferred, unhealthy[], error, checked_at}
    endpoint_status = Column(JSONB, nullable=True)
    # Operator-chosen member to try first; NULL = automatic (the configured entry point).
    preferred_node_id = Column(UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)

    site = relationship("Site", back_populates="pve_targets")
    credentials = relationship("PveCredential", back_populates="pve_target")


class PveCredential(Base):
    """A named credential slot for a PVE target (inventory / maintenance / administrative).

    The secret is stored encrypted (Fernet) via crypto.py and is never returned
    in plaintext by the API once created.
    """

    __tablename__ = "pve_credentials"

    id = uuid_pk()
    pve_target_id = Column(UUID(as_uuid=True), ForeignKey("pve_targets.id"), nullable=False)
    slot_name = Column(String, nullable=False)  # inventory | maintenance | administrative
    token_user = Column(String, nullable=False)  # e.g. pyxie-manager@pve
    token_id = Column(String, nullable=False)  # e.g. inventory
    encrypted_secret = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="untested")  # untested | valid | invalid
    last_validated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
    created_by = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("pve_target_id", "slot_name", name="uq_credential_target_slot"),
    )

    pve_target = relationship("PveTarget", back_populates="credentials")


class HostMaintenanceCredential(Base):
    """SSH credential for Stage W4 host package-management, deliberately a
    SEPARATE table/trust model from PveCredential (PVE API tokens). This is
    an SSH private key for a dedicated, non-interactive OS-level identity
    (e.g. 'pyxie-hostmaint') restricted via sudoers to ONLY the
    /usr/local/sbin/pyxie-maint wrapper -- never a general shell account,
    never reused for PVE API calls, never the same credential as
    inventory/maintenance PVE tokens. One row per PveTarget (cluster): the
    same keypair is expected to be authorized on every node in that
    cluster, since W4 connects directly to whichever node's management_ip
    (populated by discovery, same field used for PVE API endpoint failover)
    is being maintained.
    """

    __tablename__ = "host_maintenance_credentials"

    id = uuid_pk()
    pve_target_id = Column(UUID(as_uuid=True), ForeignKey("pve_targets.id"), nullable=False, unique=True)
    ssh_username = Column(String, nullable=False)
    ssh_port = Column(Integer, nullable=False, default=22)
    encrypted_private_key = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="untested")  # untested | valid | invalid
    last_validated_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_wrapper_version = Column(String, nullable=True)
    last_seen_contract_version = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
    created_by = Column(String, nullable=True)

    pve_target = relationship("PveTarget")


# ---------------------------------------------------------------------------
# Audit — full future-proof event envelope
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = uuid_pk()
    timestamp = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    actor = Column(String, nullable=True)
    actor_type = Column(String, nullable=False, default="system")  # user | system | provider
    organization_id = Column(UUID(as_uuid=True), nullable=True)
    site_id = Column(UUID(as_uuid=True), nullable=True)
    cluster_id = Column(UUID(as_uuid=True), nullable=True)
    node_id = Column(UUID(as_uuid=True), nullable=True)
    workload_id = Column(UUID(as_uuid=True), nullable=True)
    provider_id = Column(UUID(as_uuid=True), nullable=True)
    event_category = Column(String, nullable=False)  # e.g. 'system', 'auth', 'inventory', 'provider', 'settings'
    event_type = Column(String, nullable=False)  # e.g. 'inventory.discovery.completed'
    operation = Column(String, nullable=True)
    correlation_id = Column(UUID(as_uuid=True), nullable=True)
    plan_id = Column(UUID(as_uuid=True), nullable=True)
    state_before = Column(JSONB, nullable=True)
    state_after = Column(JSONB, nullable=True)
    result = Column(String, nullable=False, default="success")  # success | failure | error
    severity = Column(String, nullable=False, default="info")  # info | warning | error | critical
    error = Column(Text, nullable=True)
    rollback_classification = Column(String, nullable=True)
    justification = Column(Text, nullable=True)
    event_metadata = Column(JSONB, nullable=True)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, default=1)
    inventory_refresh_interval_seconds = Column(Integer, nullable=False, default=300)
    tls_verify_default = Column(Boolean, nullable=False, default=True)
    timezone = Column(String, nullable=False, default="UTC")
    # Global kill switch for the write-capability engine -- see the
    # Safety Contract in README.md. Every real PVE write re-checks this
    # fresh at call time (shared/pyxie_core/pve_write_client.py), not
    # just once at startup, and it is a SEPARATE gate from approving an
    # individual operation in the UI -- both must be true for any PVE
    # mutation to actually happen. Used to be an env var requiring a
    # redeploy to change; moved here so it's a real Settings-page control.
    pve_mutations_enabled = Column(Boolean, nullable=False, default=False)
    # Rightsizing's peak-safety check sizes a workload so its observed peak
    # usage lands at roughly this % of the new allocation -- see
    # rightsizing.py. Memory can be set stricter than CPU (a tight memory
    # VM pages/OOMs; a tight CPU VM just schedules slower), but both default
    # to the same value and are the operator's to tune, not a fixed
    # policy choice.
    rightsizing_cpu_peak_target_pct = Column(Integer, nullable=False, default=75)
    rightsizing_mem_peak_target_pct = Column(Integer, nullable=False, default=75)
    # Off by default: single-socket hardware doesn't have the usual
    # NUMA-locality reason to avoid odd vCPU counts, and rounding up
    # wastes allocation on smaller hosts. Exposed for whenever that
    # judgment call should flip (different hardware, a fleet-wide
    # convention the operator wants regardless).
    rightsizing_round_vcpu_even = Column(Boolean, nullable=False, default=False)
    # Outgoing email for notifications (e.g. the "send test email" check on
    # the Settings page, and future real alerts). Off by default, same
    # conservative posture as pve_mutations_enabled above. The password is
    # never stored in plaintext -- encrypt_secret()/decrypt_secret() from
    # crypto.py, same convention as every PVE/PBS credential in this app.
    smtp_enabled = Column(Boolean, nullable=False, default=False)
    smtp_host = Column(String, nullable=True)
    smtp_port = Column(Integer, nullable=False, default=587)
    smtp_username = Column(String, nullable=True)
    smtp_encrypted_password = Column(Text, nullable=True)
    smtp_from_address = Column(String, nullable=True)
    smtp_use_tls = Column(Boolean, nullable=False, default=True)
    # Where test emails and future real alert notifications go. A single
    # free-text field -- comma-separate multiple addresses if needed.
    notification_recipient = Column(String, nullable=True)
    # A finding appearing/clearing is announced only after that state has held
    # this long, so a flapping host (e.g. a node repeatedly dropping out of
    # quorum) produces one alert when it settles, not one per flip. 0 = notify
    # immediately. See findings._settle_notifications.
    notification_hold_down_minutes = Column(Integer, nullable=False, default=5)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)

    @property
    def smtp_password_set(self) -> bool:
        return bool(self.smtp_encrypted_password)


class RightsizingCache(Base):
    """Singleton cache (same id=1 pattern as AppSettings) of
    assess_all_workloads()'s last computed result. GET /api/rightsizing
    used to recompute this live on every single request -- confirmed live
    as the actual cause of multi-second Workloads/Maintenance page loads
    even after batching the underlying queries (still ~1.1s for 130
    workloads, not free). Populated by the worker's regular run_all cycle
    (piggybacking on the existing ~5-minute schedule, not a new one) and
    by the admin-only manual POST /api/rightsizing/recompute trigger --
    both call refresh_rightsizing_cache() so there's exactly one place
    that decides what gets cached."""

    __tablename__ = "rightsizing_cache"

    id = Column(Integer, primary_key=True, default=1)
    computed_at = Column(DateTime(timezone=True), nullable=False)
    assessments = Column(JSONB, nullable=False)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id = uuid_pk()
    email = Column(String, nullable=False, unique=True)
    display_name = Column(String, nullable=True)
    # Nullable: an invited-but-not-yet-accepted user has no password until
    # they complete accept-invite. invite_token is the only way in for such
    # a user -- login already requires a real password_hash match, so a
    # null password_hash is naturally unauthenticatable, not a special case.
    password_hash = Column(String, nullable=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    invite_token = Column(String, nullable=True, unique=True)
    invite_token_expires_at = Column(DateTime(timezone=True), nullable=True)


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True)  # opaque random token, not a JWT
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)


# ---------------------------------------------------------------------------
# Historical metrics -- backfilled from PVE's own RRD data, then kept current
# by each polling cycle. object_type/object_id is a loose reference (node or
# workload) rather than a hard FK so a metrics provider never has to know
# about every possible referenced table.
# ---------------------------------------------------------------------------


class MetricPoint(Base):
    __tablename__ = "metric_points"

    object_type = Column(String, primary_key=True)  # 'node' | 'workload'
    object_id = Column(UUID(as_uuid=True), primary_key=True)
    metric = Column(String, primary_key=True)  # 'cpu_pct' | 'mem_pct' | 'mem_used_bytes' | ...
    sampled_at = Column(DateTime(timezone=True), primary_key=True)
    value = Column(Float, nullable=True)
    source = Column(String, nullable=False, default="pve_rrd")


class WorkloadLiveMem(Base):
    """Latest live memory in use per running VM, as PVE's cluster resources list
    reports it (what the PVE summary screen shows). Upserted every ~30s by the
    worker's live-memory loop (migration 0038)."""

    __tablename__ = "workload_live_mem"

    workload_id = Column(UUID(as_uuid=True), ForeignKey("workloads.id", ondelete="CASCADE"), primary_key=True)
    mem_used_bytes = Column(BigInteger, nullable=False)
    sampled_at = Column(DateTime(timezone=True), nullable=False)


class MemPressureSample(Base):
    """One reading of a VM's guest memory counters (from PVE's ballooninfo), every
    ~5 minutes. The counters are cumulative since the guest booted, so what matters
    is the change between samples: a guest that is genuinely short of RAM keeps
    swapping IN; one whose memory is merely full of file cache does not. This is
    the evidence Rightsizing needs before it tells anyone to add memory, because
    PVE's "used" memory includes that cache (migration 0037)."""

    __tablename__ = "workload_mem_pressure"

    workload_id = Column(UUID(as_uuid=True), ForeignKey("workloads.id", ondelete="CASCADE"), primary_key=True)
    sampled_at = Column(DateTime(timezone=True), primary_key=True)
    swapped_in_bytes = Column(BigInteger, nullable=False, default=0)
    swapped_out_bytes = Column(BigInteger, nullable=False, default=0)
    major_faults = Column(BigInteger, nullable=False, default=0)
    free_bytes = Column(BigInteger, nullable=True)
    total_bytes = Column(BigInteger, nullable=True)


# ---------------------------------------------------------------------------
# Findings -- an observed condition, not necessarily a recommendation.
# ---------------------------------------------------------------------------


class Finding(Base):
    __tablename__ = "findings"

    id = uuid_pk()
    object_type = Column(String, nullable=True)  # cluster|node|workload|storage|task|provider
    object_id = Column(UUID(as_uuid=True), nullable=True)
    category = Column(String, nullable=False)  # cluster|quorum|ha|node|version|update|workload|storage|task|protection|configuration
    severity = Column(String, nullable=False)  # critical|warning|info|unknown
    title = Column(String, nullable=False)
    evidence = Column(JSONB, nullable=True)
    dedupe_key = Column(String, nullable=False)  # stable key so repeated scans update, not duplicate
    first_observed = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_observed = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String, nullable=False, default="system")
    confidence = Column(String, nullable=True)  # high|moderate|preliminary|insufficient_data
    finding_metadata = Column(JSONB, nullable=True)
    # When the current active/inactive state began, and whether the operator
    # was last told this finding is active -- together they drive the flap
    # hold-down for notifications (findings._settle_notifications).
    state_since = Column(DateTime(timezone=True), nullable=True)
    notified_active = Column(Boolean, nullable=True)

    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_finding_dedupe_key"),)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = uuid_pk()
    object_type = Column(String, nullable=True)
    object_id = Column(UUID(as_uuid=True), nullable=True)
    category = Column(String, nullable=False)  # health|updates|configuration|rightsizing|capacity|placement|protection|maintenance
    title = Column(String, nullable=False)
    evidence = Column(JSONB, nullable=True)
    expected_benefit = Column(Text, nullable=True)
    possible_impact = Column(Text, nullable=True)
    severity = Column(String, nullable=False, default="info")
    risk = Column(String, nullable=False, default="low")  # low|moderate|high
    confidence = Column(String, nullable=True)  # high|moderate|preliminary|insufficient_data
    observation_window_days = Column(Integer, nullable=True)
    policy_id = Column(UUID(as_uuid=True), ForeignKey("policies.id"), nullable=True)
    dedupe_key = Column(String, nullable=False)
    generated_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
    lifecycle_state = Column(String, nullable=False, default="open")  # open|acknowledged|snoozed|dismissed|resolved
    snoozed_until = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_recommendation_dedupe_key"),)


# ---------------------------------------------------------------------------
# Policies -- narrower scope overrides broader scope. Namespaced key/value,
# same "data not enum" philosophy as capabilities.
# ---------------------------------------------------------------------------


class Policy(Base):
    __tablename__ = "policies"

    id = uuid_pk()
    scope_type = Column(String, nullable=False, default="organization")
    # organization | site | cluster | node | workload
    scope_id = Column(UUID(as_uuid=True), nullable=True)
    key = Column(String, nullable=False)  # e.g. 'storage.warning_threshold_pct'
    value = Column(JSONB, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)

    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id", "key", name="uq_policy_scope_key"),
    )


# ---------------------------------------------------------------------------
# Safety rules -- informational/planning only in this pass. No executable
# write/rollback logic is attached to these; they exist so maintenance-plan
# blockers have a stable, traceable rule ID for future execution phases.
# ---------------------------------------------------------------------------


class SafetyRule(Base):
    __tablename__ = "safety_rules"

    id = Column(String, primary_key=True)  # e.g. 'SAFE-QUORUM-001'
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String, nullable=False)


# ---------------------------------------------------------------------------
# Protection subsystem
# ---------------------------------------------------------------------------


class ProtectionTarget(Base):
    """A configured connection to a protection provider instance (one PBS
    server, one Veeam server, one Commvault environment, ...)."""

    __tablename__ = "protection_targets"

    id = uuid_pk()
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("providers.id"), nullable=False)
    name = Column(String, nullable=False)
    hostname = Column(String, nullable=False)
    api_port = Column(Integer, nullable=False, default=8007)
    tls_verify = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class ProtectionCredential(Base):
    __tablename__ = "protection_credentials"

    id = uuid_pk()
    protection_target_id = Column(UUID(as_uuid=True), ForeignKey("protection_targets.id"), nullable=False)
    slot_name = Column(String, nullable=False, default="inventory")
    token_user = Column(String, nullable=False)
    token_id = Column(String, nullable=False)
    encrypted_secret = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="untested")
    last_validated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)

    __table_args__ = (
        UniqueConstraint("protection_target_id", "slot_name", name="uq_protection_cred_target_slot"),
    )


class ProtectionResult(Base):
    """Normalized protection status for one workload, from one provider."""

    __tablename__ = "protection_results"

    id = uuid_pk()
    workload_id = Column(UUID(as_uuid=True), ForeignKey("workloads.id"), nullable=False)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("providers.id"), nullable=False)
    protected = Column(String, nullable=False, default="unknown")  # true|false|unknown -- UNKNOWN is not SAFE
    last_successful_job_at = Column(DateTime(timezone=True), nullable=True)
    last_restore_point_at = Column(DateTime(timezone=True), nullable=True)
    active_operation = Column(Boolean, nullable=True)
    next_known_operation_at = Column(DateTime(timezone=True), nullable=True)
    sla_defined = Column(Boolean, nullable=True)
    sla_compliant = Column(String, nullable=False, default="unknown")
    restore_verified = Column(String, nullable=False, default="unknown")
    confidence = Column(String, nullable=True)
    known_limitations = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    last_updated = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)

    __table_args__ = (
        UniqueConstraint("workload_id", "provider_id", name="uq_protection_result_workload_provider"),
    )


# ---------------------------------------------------------------------------
# Maintenance planner -- read only. No execution fields exist anywhere here.
# ---------------------------------------------------------------------------


class MaintenancePlan(Base):
    __tablename__ = "maintenance_plans"

    id = uuid_pk()
    node_id = Column(UUID(as_uuid=True), ForeignKey("nodes.id"), nullable=False)
    generated_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    inventory_state_at = Column(DateTime(timezone=True), nullable=True)
    protection_state_at = Column(DateTime(timezone=True), nullable=True)
    metric_state_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=False, default="current")  # current|stale
    summary = Column(JSONB, nullable=False)
    blocking_safety_rules = Column(JSONB, nullable=True)
    created_by = Column(String, nullable=True)


class MaintenancePlanWorkload(Base):
    __tablename__ = "maintenance_plan_workloads"

    id = uuid_pk()
    plan_id = Column(UUID(as_uuid=True), ForeignKey("maintenance_plans.id"), nullable=False)
    workload_id = Column(UUID(as_uuid=True), ForeignKey("workloads.id"), nullable=False)
    classification = Column(String, nullable=False)
    # live_migratable | offline_migration_required | shutdown_in_place | blocked | unknown
    reasons = Column(JSONB, nullable=True)
    blocking_safety_rules = Column(JSONB, nullable=True)


# ---------------------------------------------------------------------------
# Operations engine -- the write-capability safety foundation (Stage W0).
#
# operation_types is seeded data (same "data not enum" philosophy as
# capabilities/policies): each row names the ordered stage list a given
# operation type walks through. operations is the persistent, resumable
# state machine instance -- current stage + which stages are done/pending
# live in the row itself so a worker/API/container restart can reload an
# in-flight operation and continue rather than losing or re-starting it.
# Nothing here executes anything by itself; pyxie_core.operations_engine and
# the per-operation-type workflow modules (e.g. migration_workflow.py) are
# what actually walk an operation through its stages.
# ---------------------------------------------------------------------------


class OperationType(Base):
    __tablename__ = "operation_types"

    id = Column(String, primary_key=True)  # e.g. 'vm.live_migrate', 'node.evacuate'
    category = Column(String, nullable=False)  # migration | lifecycle | patching | reboot | maintenance
    description = Column(Text, nullable=True)
    stages = Column(JSONB, nullable=False)  # ordered list of stage-name strings
    default_rollback_classification = Column(String, nullable=False, default="unknown")
    # auto_reversible | manual_reversible | irreversible | unknown
    required_credential_slot = Column(String, nullable=False, default="maintenance")
    requires_individual_approval = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=False)
    # enabled=False means the type is seeded/reserved but has no workflow
    # implementation wired up yet -- create_operation refuses to create an
    # instance of a disabled type.


class Operation(Base):
    """One instance of a state machine walking through operation_type.stages.

    status is the coarse machine state (pending/awaiting_approval/approved/
    revalidating/executing/monitoring/verifying/completed/failed/blocked/
    cancelled); stage is the specific named stage within that -- e.g. a
    'monitoring' status might sit at stage 'wait_for_pve_task' for a long
    time across worker restarts. stages_completed/stages_pending are kept in
    sync with operation_type.stages so the UI and a resuming worker can both
    render/derive progress without re-deriving it from audit_events.
    """

    __tablename__ = "operations"

    id = uuid_pk()
    operation_type_id = Column(String, ForeignKey("operation_types.id"), nullable=False)
    correlation_id = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    # shared across all operations that belong to one logical run (e.g. every
    # per-VM migrate operation under one node evacuation, or every stage
    # operation under one W6 maintenance run) so audit/UI can group them.
    plan_id = Column(UUID(as_uuid=True), ForeignKey("maintenance_plans.id"), nullable=True)
    parent_operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)

    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id"), nullable=True)
    node_id = Column(UUID(as_uuid=True), ForeignKey("nodes.id"), nullable=True)
    workload_id = Column(UUID(as_uuid=True), ForeignKey("workloads.id"), nullable=True)

    status = Column(String, nullable=False, default="pending")
    # pending | dry_run | awaiting_approval | approved | revalidating |
    # executing | monitoring | verifying | completed | failed | blocked | cancelled
    stage = Column(String, nullable=True)
    stages_completed = Column(JSONB, nullable=False, default=list)
    stages_pending = Column(JSONB, nullable=False, default=list)

    context = Column(JSONB, nullable=True)  # input parameters (e.g. {"target_node": "pve-node-2"})
    dry_run_result = Column(JSONB, nullable=True)
    progress = Column(JSONB, nullable=True)  # periodically-updated {transferred_bytes, total_bytes, rate_bytes_per_sec, pct, raw_line} during 'monitoring'
    precondition_snapshot = Column(JSONB, nullable=True)  # fresh state read immediately before execution
    pve_upid = Column(String, nullable=True)
    pve_task_result = Column(JSONB, nullable=True)
    verification_result = Column(JSONB, nullable=True)

    rollback_classification = Column(String, nullable=True)
    blocking_safety_rules = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)

    created_by = Column(String, nullable=True)
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    dismissed = Column(Boolean, nullable=False, default=False)  # hidden from the default list, never deleted

    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class PlacementAffinityRule(Base):
    """PyXie-level keep_together/keep_apart placement policy -- independent
    of PVE's own native HA affinity rules (see check_crs_affinity), so it
    applies even to workloads never enrolled in PVE HA (most of them, in
    practice). A rule targets either an explicit pair of workloads, or every
    workload sharing a given tag (tag_group) -- e.g. all workloads tagged
    'dns' should keep_apart so a single node failure can't take down every
    resolver. strict=True is a hard placement block; strict=False is a
    scoring-only soft preference.
    """

    __tablename__ = "placement_affinity_rules"

    id = uuid_pk()
    rule_type = Column(String, nullable=False)  # keep_together | keep_apart
    scope_type = Column(String, nullable=False)  # workload_pair | tag_group
    workload_ids = Column(JSONB, nullable=True)  # [uuid, uuid] for workload_pair
    tag = Column(String, nullable=True)  # for tag_group
    strict = Column(Boolean, nullable=False, default=True)
    description = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class ResourceLock(Base):
    """A logical lock PyXie takes on a resource (node/workload/cluster) while
    an operation is in flight, so PyXie cannot fight itself (e.g. two
    concurrent operations targeting the same node). This does not and cannot
    prevent changes made directly against PVE outside of PyXie -- every
    operation must still revalidate live state immediately before acting.
    """

    __tablename__ = "resource_locks"

    id = uuid_pk()
    resource_type = Column(String, nullable=False)  # cluster | node | workload
    resource_id = Column(UUID(as_uuid=True), nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    reason = Column(String, nullable=True)
    acquired_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    released_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class Notification(Base):
    __tablename__ = "notifications"

    id = uuid_pk()
    severity = Column(String, nullable=False, default="informational")  # informational|warning|critical
    title = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    object_type = Column(String, nullable=True)
    object_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(String, nullable=False, default="unread")  # unread|read|dismissed
    source = Column(String, nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)


class NotificationRule(Base):
    """One row of Settings > Notifications: which findings get emailed, and
    to whom. In-app Notification rows are always written for warning/critical
    findings regardless of rules -- rules only govern email."""

    __tablename__ = "notification_rules"

    id = uuid_pk()
    name = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    # Finding.category values this rule covers; empty list means every category.
    categories = Column(JSONB, nullable=False, default=list)
    min_severity = Column(String, nullable=False, default="critical")  # warning|critical
    send_recovery = Column(Boolean, nullable=False, default=True)
    recipients = Column(JSONB, nullable=False, default=list)  # explicit email addresses
    include_admins = Column(Boolean, nullable=False, default=False)  # also every active admin user
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


# ---------------------------------------------------------------------------
# Reporting inventory (RVTools-style). Per-workload detail PVE only exposes
# through per-VM config/snapshot/agent calls, collected on its own schedule
# by pyxie_core.report_collection (not inside discovery, which has to stay
# fast) and read back by the Reporting page. Every row carries the time it
# was collected so the UI can say how fresh it is.
# ---------------------------------------------------------------------------


class WorkloadConfig(Base):
    __tablename__ = "workload_configs"

    workload_id = Column(UUID(as_uuid=True), ForeignKey("workloads.id", ondelete="CASCADE"), primary_key=True)
    collected_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    sockets = Column(Integer, nullable=True)
    cores_per_socket = Column(Integer, nullable=True)
    cpu_type = Column(String, nullable=True)
    memory_mb = Column(Integer, nullable=True)
    balloon_mb = Column(Integer, nullable=True)
    machine = Column(String, nullable=True)
    bios = Column(String, nullable=True)
    onboot = Column(Boolean, nullable=True)
    protection = Column(Boolean, nullable=True)
    agent_enabled = Column(Boolean, nullable=True)
    description = Column(Text, nullable=True)
    hostname = Column(String, nullable=True)  # lxc hostname
    guest_ips = Column(JSONB, nullable=True)  # list of addresses (guest agent / lxc interfaces)
    guest_ips_source = Column(String, nullable=True)  # agent | lxc | config | unavailable


class WorkloadDisk(Base):
    __tablename__ = "workload_disks"

    id = uuid_pk()
    workload_id = Column(UUID(as_uuid=True), ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False)
    slot = Column(String, nullable=False)  # scsi0, virtio1, rootfs, mp0, efidisk0, ...
    bus = Column(String, nullable=True)
    storage = Column(String, nullable=True)
    volume = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    is_cdrom = Column(Boolean, nullable=False, default=False)
    cache = Column(String, nullable=True)
    discard = Column(String, nullable=True)
    ssd = Column(Boolean, nullable=True)
    iothread = Column(Boolean, nullable=True)
    backup = Column(Boolean, nullable=True)  # false = excluded from backup (PVE backup=0)
    collected_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)

    __table_args__ = (UniqueConstraint("workload_id", "slot", name="uq_workload_disk_slot"),)


class WorkloadNic(Base):
    __tablename__ = "workload_nics_report"

    id = uuid_pk()
    workload_id = Column(UUID(as_uuid=True), ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False)
    slot = Column(String, nullable=False)  # net0, net1, ...
    model = Column(String, nullable=True)  # virtio, e1000, veth, ...
    mac = Column(String, nullable=True)
    bridge = Column(String, nullable=True)
    vlan_tag = Column(Integer, nullable=True)
    firewall = Column(Boolean, nullable=True)
    rate_mbps = Column(Float, nullable=True)
    link_down = Column(Boolean, nullable=True)
    ip_config = Column(String, nullable=True)  # lxc static/dhcp setting from config
    ips = Column(JSONB, nullable=True)  # addresses seen on this interface (agent/lxc)
    collected_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)

    __table_args__ = (UniqueConstraint("workload_id", "slot", name="uq_workload_nic_report_slot"),)


class WorkloadSnapshot(Base):
    __tablename__ = "workload_snapshots"

    id = uuid_pk()
    workload_id = Column(UUID(as_uuid=True), ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    parent = Column(String, nullable=True)
    snapshot_time = Column(DateTime(timezone=True), nullable=True)
    includes_ram = Column(Boolean, nullable=True)
    collected_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)

    __table_args__ = (UniqueConstraint("workload_id", "name", name="uq_workload_snapshot_name"),)


class ReportCollectionRun(Base):
    __tablename__ = "report_collection_runs"

    id = uuid_pk()
    started_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=False, default="running")  # running | success | partial | failed
    triggered_by = Column(String, nullable=False, default="schedule")  # schedule | email of the user who hit Refresh
    workloads_total = Column(Integer, nullable=False, default=0)
    workloads_collected = Column(Integer, nullable=False, default=0)
    workloads_failed = Column(Integer, nullable=False, default=0)
    summary = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Internal job run log -- distinguishes PyXie's own background jobs from PVE
# tasks on the Tasks page.
# ---------------------------------------------------------------------------


class InternalJobRun(Base):
    __tablename__ = "internal_job_runs"

    id = uuid_pk()
    job_name = Column(String, nullable=False)  # discovery | metrics_collection | recommendation_generation
    started_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=False, default="running")  # running|success|failed
    result_summary = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
