"""Pure coverage for the operation-message helpers."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.labels import already_in_desired_state, child_reason, vm_label


def test_vm_label_from_plan_item_and_workload():
    assert vm_label({"name": "st-jellyfin", "vmid": 11159}) == "st-jellyfin (VM 11159)"
    assert vm_label({"workload_name": "st-hermes", "vmid": 7}) == "st-hermes (VM 7)"
    assert vm_label(SimpleNamespace(name="ST-W11", vmid=11167)) == "ST-W11 (VM 11167)"


def test_vm_label_falls_back_when_name_unknown():
    assert vm_label({"name": None, "vmid": 11159}) == "VM 11159"
    assert vm_label({"vmid": 5}) == "VM 5"
    assert vm_label({}) == "the VM"


def test_child_reason_prefers_error_then_rules_then_status():
    assert child_reason(SimpleNamespace(error="PVE task failed", blocking_safety_rules=["SAFE-MIGRATE-001"], status="failed")) == "PVE task failed"
    assert child_reason(SimpleNamespace(error=None, blocking_safety_rules=["SAFE-MIGRATE-001"], status="blocked")) == "blocked by SAFE-MIGRATE-001 (Migration eligibility)"
    assert child_reason(SimpleNamespace(error="", blocking_safety_rules=["SAFE-NEW-999", "SAFE-HA-001"], status="blocked")) == "blocked by SAFE-NEW-999, SAFE-HA-001 (HA resource evacuation)"
    assert "no reason recorded" in child_reason(SimpleNamespace(error=None, blocking_safety_rules=None, status="blocked"))


def test_already_in_desired_state():
    assert already_in_desired_state("workload.shutdown", "stopped")
    assert already_in_desired_state("workload.force_stop", "stopped")
    assert already_in_desired_state("workload.start", "running")
    assert not already_in_desired_state("workload.shutdown", "running")
    assert not already_in_desired_state("workload.start", "stopped")
    assert not already_in_desired_state("workload.reboot", "running")  # a reboot is never a no-op
    assert not already_in_desired_state("workload.shutdown", None)
