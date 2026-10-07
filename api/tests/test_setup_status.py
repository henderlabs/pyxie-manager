"""Pure coverage for the setup guide's step logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.setup_status import build_steps, progress


def by_key(steps):
    return {s["key"]: s for s in steps}


def test_empty_install_points_at_site_first():
    s = by_key(build_steps({"sites": 0, "targets": [], "nodes": [], "hostmaint": [], "settings": {}}))
    assert s["sites"]["state"] == "next"
    assert s["accounts"]["state"] == "todo"
    assert s["cluster"]["state"] == "waiting"
    assert s["hosts"]["state"] == "waiting"


def test_site_done_then_accounts_is_next():
    steps = build_steps({"sites": 1, "targets": [], "nodes": [], "hostmaint": [], "settings": {}})
    s = by_key(steps)
    assert s["sites"]["state"] == "done" and s["accounts"]["state"] == "next"
    assert [x["number"] for x in steps] == list(range(1, 9))


def test_target_with_valid_inventory_marks_accounts_and_cluster_done():
    t = {"id": "t", "name": "Lab", "inventory_status": "valid", "maintenance_status": None, "nodes": 4}
    s = by_key(build_steps({"sites": 1, "targets": [t], "nodes": [], "hostmaint": [], "settings": {}}))
    assert s["accounts"]["state"] == "done" and s["cluster"]["state"] == "done"
    assert s["admin"]["state"] == "optional"


def test_untested_target_is_cluster_todo_and_next():
    t = {"id": "t", "name": "Lab", "inventory_status": "untested", "nodes": 0}
    s = by_key(build_steps({"sites": 1, "targets": [t], "nodes": [], "hostmaint": [], "settings": {}}))
    assert s["cluster"]["state"] == "next" and "untested" in s["cluster"]["summary"]


def test_admin_credential_states():
    base = {"id": "t", "name": "Lab", "inventory_status": "valid", "nodes": 2}
    inp = lambda m: {"sites": 1, "targets": [{**base, "maintenance_status": m}], "nodes": [], "hostmaint": [], "settings": {}}
    assert by_key(build_steps(inp("valid")))["admin"]["state"] == "done"
    assert by_key(build_steps(inp("untested")))["admin"]["state"] in ("todo", "next")


def test_hosts_progress_counts_ready_nodes_and_flags_outdated_wrapper():
    t = {"id": "t", "name": "Lab", "inventory_status": "valid", "maintenance_status": "valid", "nodes": 3}
    nodes = [
        {"name": "n1", "pinned": True, "wrapper_version": "1.1.0"},
        {"name": "n2", "pinned": True, "wrapper_version": "1.0.0"},
        {"name": "n3", "pinned": False, "wrapper_version": None},
    ]
    s = by_key(build_steps({"sites": 1, "targets": [t], "nodes": nodes, "hostmaint": [{"target_id": "t", "has_cred": True}],
                            "expected_wrapper": "1.1.0", "settings": {}}))
    h = s["hosts"]
    assert h["state"] in ("todo", "next") and "1 of 3" in h["summary"]
    details = {i["name"]: (i["ok"], i["detail"]) for i in h["items"]}
    assert details["n1"][0] is True
    assert "update available" in details["n2"][1] and not details["n2"][0]
    assert "not pinned" in details["n3"][1]


def test_hosts_without_keypair_say_generate_it():
    t = {"id": "t", "name": "Lab", "inventory_status": "valid", "nodes": 2}
    s = by_key(build_steps({"sites": 1, "targets": [t], "nodes": [{"name": "n1", "pinned": False}], "hostmaint": [], "settings": {}}))
    assert "generate the host key pair" in s["hosts"]["summary"].lower()


def test_all_nodes_ready_is_done_and_features_notifications_follow_settings():
    t = {"id": "t", "name": "Lab", "inventory_status": "valid", "maintenance_status": "valid", "nodes": 1}
    steps = build_steps({"sites": 1, "targets": [t], "nodes": [{"name": "n1", "pinned": True, "wrapper_version": "1.1.0"}],
                         "hostmaint": [{"target_id": "t", "has_cred": True}], "expected_wrapper": "1.1.0",
                         "settings": {"pve_mutations_enabled": True, "smtp_enabled": True, "notification_recipient_set": True, "notification_rules": 2}})
    s = by_key(steps)
    assert s["hosts"]["state"] == "done" and s["features"]["state"] == "done" and s["notifications"]["state"] == "done"
    assert progress(steps) == {"done": 7, "total": 7}
    assert not any(x["state"] == "next" for x in steps)


def test_only_one_next_marker():
    steps = build_steps({"sites": 0, "targets": [], "nodes": [], "hostmaint": [], "settings": {}})
    assert sum(1 for s in steps if s["state"] == "next") == 1


def test_hosts_substeps_in_order_without_a_keypair():
    t = {"id": "t", "name": "Lab", "inventory_status": "valid", "nodes": 2}
    s = by_key(build_steps({"sites": 1, "targets": [t], "nodes": [{"name": "n1", "pinned": False}, {"name": "n2", "pinned": False}],
                            "hostmaint": [], "expected_wrapper": "1.1.0", "settings": {}}))
    sub = {x["key"]: x for x in s["hosts"]["substeps"]}
    assert sub["keypair"]["state"] == "todo" and sub["keypair"]["action"]["kind"] == "generate_keypair"
    assert sub["keypair"]["action"]["target_id"] == "t"
    assert sub["script"]["state"] == "waiting" and sub["script"]["action"] is None
    assert sub["pin"]["state"] == "waiting"
    assert "generate the host key pair" in s["hosts"]["summary"].lower()


def test_hosts_substeps_after_keypair_point_at_builder_then_pinning():
    t = {"id": "t", "name": "Lab", "inventory_status": "valid", "nodes": 2}
    nodes = [{"name": "n1", "pinned": True, "wrapper_version": "1.1.0"}, {"name": "n2", "pinned": False, "wrapper_version": None}]
    s = by_key(build_steps({"sites": 1, "targets": [t], "nodes": nodes, "hostmaint": [{"target_id": "t", "has_cred": True}],
                            "expected_wrapper": "1.1.0", "settings": {}}))
    sub = {x["key"]: x for x in s["hosts"]["substeps"]}
    assert sub["keypair"]["state"] == "done"
    assert sub["script"]["state"] == "todo" and sub["script"]["action"]["anchor"] == "builder"
    assert "1 of 2" in sub["script"]["detail"]
    assert sub["pin"]["state"] == "todo" and sub["pin"]["action"]["href"] == "/platform/credentials"
    assert "1 of 2" in sub["pin"]["detail"]
    assert s["hosts"]["state"] in ("todo", "next")


def test_hosts_all_three_done_is_done():
    t = {"id": "t", "name": "Lab", "inventory_status": "valid", "nodes": 1}
    s = by_key(build_steps({"sites": 1, "targets": [t], "nodes": [{"name": "n1", "pinned": True, "wrapper_version": "1.1.0"}],
                            "hostmaint": [{"target_id": "t", "has_cred": True}], "expected_wrapper": "1.1.0", "settings": {}}))
    assert s["hosts"]["state"] == "done" and all(x["state"] == "done" for x in s["hosts"]["substeps"])
