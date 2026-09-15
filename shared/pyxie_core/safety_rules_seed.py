"""Seed data for the safety-rule framework. Informational/planning rules
only in this pass -- no write/execution logic is attached to any of these.
They exist so a maintenance plan's blockers carry a stable, traceable rule
ID that a future execution phase can reference.
"""

SAFETY_RULES = [
    ("SAFE-QUORUM-001", "Preserve cluster quorum", "Block or warn if removing a node would drop the cluster below quorum, or leave no margin for a second failure.", "quorum"),
    ("SAFE-HA-001", "HA resource evacuation", "An HA-managed workload must be cleanly relocated or have HA accounted for before its host is taken down.", "ha"),
    ("SAFE-STORAGE-001", "Storage locality", "A workload on node-local storage cannot live-migrate; it requires offline migration or must stay in place.", "storage"),
    ("SAFE-MIGRATE-001", "Migration eligibility", "A workload must have a compatible, capacity-eligible destination before migration is considered safe.", "migration"),
    ("SAFE-BACKUP-001", "No active protection operation", "Maintenance should not begin while a protection job for an affected workload is actively running.", "protection"),
    ("SAFE-BACKUP-002", "No imminent protection schedule", "Maintenance should avoid a window where a scheduled protection job is expected to start imminently.", "protection"),
    ("SAFE-REBOOT-001", "Reboot impact", "A pending update that requires a reboot must account for full workload evacuation first.", "update"),
    ("SAFE-ROLLBACK-001", "Rollback path", "A maintenance action should have a known rollback/recovery path before proceeding.", "execution"),
    ("SAFE-CRED-001", "Credential handling", "Credentials used for any maintenance action must be scoped, encrypted at rest, and never logged in plaintext.", "credential"),
    ("SAFE-BREAKGLASS-001", "Break-glass path", "An emergency override path must be identifiable and separately audited before any automated execution exists.", "execution"),
    ("SAFE-AUDIT-001", "Full audit trail", "Every maintenance action must be captured in the full audit envelope, including plan lineage via plan_id/correlation_id.", "audit"),
    ("SAFE-PROTECTION-001", "Backup job must cover something", "A PBS backup job's membership change must not leave it with zero guests selected and not in all-guests mode.", "protection"),
    ("SAFE-PROTECTION-002", "No membership drift since dry-run", "A backup job's live membership must still match what was previewed at dry-run time before an approved change is applied.", "protection"),
]
