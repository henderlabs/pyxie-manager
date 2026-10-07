"""Plain-words helpers for operation messages: name the VM, not just its number, and say why a step was blocked."""

from .safety_rules_seed import SAFETY_RULES

_RULE_TITLES = {r[0]: r[1] for r in SAFETY_RULES}


def vm_label(thing) -> str:
    """'st-jellyfin (VM 11159)' from a plan item (dict with name/vmid) or a Workload; 'VM 11159' if the name is unknown."""
    if isinstance(thing, dict):
        name, vmid = thing.get("name") or thing.get("workload_name"), thing.get("vmid")
    else:
        name, vmid = getattr(thing, "name", None), getattr(thing, "vmid", None)
    if name and vmid is not None:
        return f"{name} (VM {vmid})"
    return f"VM {vmid}" if vmid is not None else (name or "the VM")


def child_reason(child) -> str:
    """Why a child operation did not complete: its error, else the safety rule that blocked it, else its status."""
    err = getattr(child, "error", None)
    if err:
        return str(err)
    rules = list(getattr(child, "blocking_safety_rules", None) or [])
    if rules:
        return "blocked by " + ", ".join(f"{r} ({_RULE_TITLES[r]})" if r in _RULE_TITLES else r for r in rules)
    return f"it ended as '{getattr(child, 'status', 'unknown')}' with no reason recorded"


_DESIRED_AFTER = {"workload.shutdown": "stopped", "workload.force_stop": "stopped", "workload.start": "running"}


def already_in_desired_state(operation_type: str, live_status) -> bool:
    """True when the VM is already where this action would put it (for example, a shutdown of a VM that is already
    stopped because an earlier step in the same maintenance run shut it down). There is nothing left to do."""
    return _DESIRED_AFTER.get(operation_type) == live_status
