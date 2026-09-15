"""Rollback classification vocabulary for operations and audit events.

Every mutating operation type declares a default classification (see
operation_types.default_rollback_classification); a specific operation
instance may narrow it once more is known (e.g. a migration that already
completed successfully before failing verification might reclassify as
manual_reversible rather than the type default of auto_reversible).
"""

AUTO_REVERSIBLE = "auto_reversible"       # PyXie itself can safely reverse this (e.g. migrate back)
MANUAL_REVERSIBLE = "manual_reversible"   # reversible, but needs an operator to do it
IRREVERSIBLE = "irreversible"             # cannot be undone (e.g. a forced stop, a reboot)
UNKNOWN = "unknown"                       # not yet classified -- never treated as safe-to-retry

ALL = {AUTO_REVERSIBLE, MANUAL_REVERSIBLE, IRREVERSIBLE, UNKNOWN}
