"""Whether a VM has the memory-balloon device, in one place for the Workloads table,
the pInfo report and the pHealth check.

PVE config `balloon: 0` means NO balloon device: PVE cannot ask the guest what it
uses and reports the host-side process size (~100% for most VMs). Absent or any
other value means the device is configured (the value is the guest minimum in MiB;
absent = same as the VM maximum). A device only becomes live at the VM's next
start/PVE-initiated reboot, so a running VM that is configured "on" but still has no
guest memory stats is "pending".
"""

from typing import Optional

OFF = "off"
ON = "on"
PENDING = "pending"


def balloon_state(
    wtype: str, status: Optional[str], balloon_mb: Optional[int], mem_guest_stats: Optional[bool]
) -> Optional[str]:
    """off | on | pending | None (containers / no config collected yet)."""
    if wtype != "vm":
        return None
    if balloon_mb == 0:
        return OFF
    if status == "running" and mem_guest_stats is False:
        return PENDING
    return ON


def balloon_label(state: Optional[str], balloon_mb: Optional[int], memory_mb: Optional[int]) -> Optional[str]:
    if state is None:
        return None
    if state == OFF:
        return "Off"
    minimum = balloon_mb if balloon_mb else memory_mb
    detail = f"min {minimum / 1024:g} GiB" if minimum else "on"
    if state == PENDING:
        return f"Configured ({detail}), not active yet"
    return f"On ({detail})"
