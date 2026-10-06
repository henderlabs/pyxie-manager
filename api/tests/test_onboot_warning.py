"""A stopped VM with Start at boot on must be flagged in maintenance previews.

Pure logic: no database."""

from pyxie_core.maintenance import flag_onboot, onboot_restart_reason


def test_flags_when_onboot_is_on():
    for value in ("1", 1, "yes", "true", "on"):
        assert onboot_restart_reason(123, {"onboot": value})


def test_not_flagged_when_off_or_missing():
    assert onboot_restart_reason(123, {"onboot": "0"}) is None
    assert onboot_restart_reason(123, {}) is None
    assert onboot_restart_reason(123, None) is None


def test_flag_onboot_puts_the_warning_first_and_marks_the_item():
    item = {"workload_id": "w", "vmid": 123, "name": "x", "reasons": ["stopped, and its disk is on shared storage"]}
    out = flag_onboot(item, {"onboot": 1})
    assert out["starts_on_boot"] is True
    assert "qm set 123 --onboot 0" in out["reasons"][0]
    assert out["reasons"][1] == "stopped, and its disk is on shared storage"
    assert "starts_on_boot" not in item


def test_flag_onboot_leaves_other_items_untouched():
    item = {"workload_id": "w", "vmid": 123, "name": "x", "reasons": ["r"]}
    assert flag_onboot(item, {"onboot": 0}) is item
