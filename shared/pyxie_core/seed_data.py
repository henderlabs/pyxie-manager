"""Seed data for provider categories and the capability catalog.

Capabilities are namespaced strings stored as database rows, never a
hard-coded application enum, so a future capability can be added with a
plain INSERT and no schema change.
"""

PROVIDER_CATEGORIES = [
    ("pve", "Proxmox VE cluster management"),
    ("protection", "Backup and data protection"),
    ("monitoring", "Infrastructure and application monitoring"),
    ("notification", "Outbound alerting and messaging"),
    ("itsm", "IT service management / ticketing"),
    ("authentication", "Identity and access providers"),
    ("hardware", "Out-of-band hardware management"),
]

CAPABILITIES = [
    # PVE -- read capabilities implemented in Phase 0
    ("pve.inventory.read", "pve", "Read cluster/node/workload/storage inventory"),
    ("pve.health.read", "pve", "Read node and cluster health/status"),
    ("pve.tasks.read", "pve", "Read recent PVE task history"),
    # PVE -- write capabilities reserved for future phases, NOT implemented now
    ("pve.migration", "pve", "Migrate a workload between nodes"),
    ("pve.vm.control", "pve", "Start/stop/shutdown a VM or container"),
    ("pve.node.update", "pve", "Apply package updates to a node"),
    ("pve.node.reboot", "pve", "Reboot a node"),
    ("pve.storage.modify", "pve", "Modify storage configuration"),
    ("pve.ha.modify", "pve", "Modify HA group/resource configuration"),
    # Protection providers -- reserved, no provider implemented in Phase 0
    ("protection.status.read", "protection", "Read backup job status"),
    ("protection.jobs.active.read", "protection", "Read currently running backup jobs"),
    ("protection.schedule.read", "protection", "Read backup schedules"),
    ("protection.jobs.membership.write", "protection", "Add/remove VMs from a PBS-backed vzdump job, or switch it to all-guests mode"),
    # Notification -- reserved
    ("notification.send", "notification", "Send an outbound notification"),
    # ITSM -- reserved
    ("itsm.ticket.create", "itsm", "Create a ticket in an ITSM system"),
    # Hardware -- reserved
    ("hardware.power.control", "hardware", "Control out-of-band power state"),
]

# Provider type -> category, contract_version, whether Phase 0 implements it.
PROVIDER_TYPE_CATALOG = [
    ("pve", "pve", 1, True),
    ("pbs", "protection", 1, False),
    ("veeam", "protection", 1, False),
    ("commvault", "protection", 1, False),
]
