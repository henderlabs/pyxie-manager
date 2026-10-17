from pyxie_core.backup_policy_workflow import apply_change, build_plan
from pyxie_core.backup_policy import validate_policy


def pol(pid, name, **kw):
    clean, _ = validate_policy({"name": name, "storage": "st-pbs", "schedule": "02:00", "keep_daily": 7, **kw})
    return {**clean, "id": pid, "job_id": "pyxie-" + name.lower()}


def test_apply_save_and_name_clash():
    pols, asg, errs = apply_change([], [], {"action": "save", "new_id": "n1", "policy": {"name": "Gold", "storage": "st-pbs", "schedule": "02:00", "keep_daily": 7}})
    assert not errs and pols[0]["id"] == "n1" and pols[0]["job_id"] == "pyxie-gold"
    _, _, errs = apply_change(pols, [], {"action": "save", "new_id": "n2", "policy": {"name": "gold", "storage": "st-pbs", "schedule": "02:00"}})
    assert any("already exists" in e for e in errs)
    pols2, _, errs = apply_change(pols, [], {"action": "save", "policy": {"id": "n1", "name": "Gold", "storage": "st-pbs", "schedule": "03:00"}})
    assert not errs and pols2[0]["schedule"] == "03:00" and len(pols2) == 1
    assert apply_change(pols, [], {"action": "save", "policy": {"id": "ghost", "name": "X", "storage": "s", "schedule": "02:00"}})[2]


def test_apply_delete_and_assign():
    pols = [pol("p1", "Gold"), pol("p2", "Silver")]
    asg = [{"policy_id": "p1", "scope_type": "cluster", "scope_value": None, "priority": 0, "policy_name": "Gold"}, {"policy_id": "p2", "scope_type": "tag", "scope_value": "db", "priority": 0, "policy_name": "Silver"}]
    p2, a2, errs = apply_change(pols, asg, {"action": "delete", "policy_id": "p1"})
    assert errs and "still in use" in errs[0]  # p1 is the default: it cannot be deleted while anything uses it
    p2, a2, errs = apply_change([pol("p1", "Gold"), pol("p3", "Spare")], asg, {"action": "delete", "policy_id": "p3"})
    assert not errs and [p["id"] for p in p2] == ["p1"]
    _, a3, errs = apply_change(pols, asg, {"action": "assign", "set": [{"scope_type": "vm", "scope_value": "w1", "policy_id": None}], "clear": [{"scope_type": "tag", "scope_value": "db"}]})
    assert not errs and {(a["scope_type"], a["policy_id"]) for a in a3} == {("cluster", "p1"), ("vm", None)}
    assert apply_change(pols, asg, {"action": "assign", "set": [{"scope_type": "cluster", "policy_id": None}]})[2]
    assert apply_change(pols, asg, {"action": "assign", "set": [{"scope_type": "tag", "scope_value": "x", "policy_id": "ghost"}]})[2]


def test_build_plan_end_to_end():
    pols = [pol("p1", "Gold")]
    asg = [{"policy_id": "p1", "scope_type": "tag", "scope_value": "prod", "priority": 0, "policy_name": "Gold"}]
    gs = [{"id": "a", "vmid": 101, "name": "a", "tags": ["prod"], "type": "vm"}, {"id": "b", "vmid": 102, "name": "b", "tags": [], "type": "vm"}]
    jobs = [{"id": "nightly", "all": 1}]
    plan = build_plan(pols, asg, gs, jobs, True)
    assert plan["membership"] == {"p1": [101]} and plan["writes"][0]["action"] == "create" and plan["writes"][0]["params"]["vmid"] == "101"
    assert plan["others"] == [{"job_id": "nightly", "action": "exclude", "vmids": [101]}]
    assert build_plan(pols, asg, gs, jobs, False)["others"] == []


def test_assignment_input_is_validated():
    pols = [pol("p1", "Gold")]
    assert apply_change(pols, [], {"action": "assign", "set": [{"scope_type": "tag", "scope_value": "bad tag!", "policy_id": "p1"}]})[2]
    assert apply_change(pols, [], {"action": "assign", "set": [{"scope_type": "vm", "scope_value": "ghost", "policy_id": "p1"}]}, {"w1"})[2]
    assert apply_change(pols, [], {"action": "assign", "set": [{"scope_type": "tag", "scope_value": "db", "policy_id": "p1", "priority": "abc"}]})[2]
    ok = apply_change(pols, [], {"action": "assign", "set": [{"scope_type": "vm", "scope_value": "w1", "policy_id": "p1", "priority": "3"}]}, {"w1"})
    assert not ok[2] and ok[1][0]["priority"] == 3


def test_a_name_clash_with_a_hand_made_job_is_a_conflict_not_a_crash():
    pols = [pol("p1", "Mine")]
    asg = [{"policy_id": "p1", "scope_type": "cluster", "scope_value": None, "priority": 0, "policy_name": "Mine"}]
    gs = [{"id": "a", "vmid": 101, "name": "a", "tags": [], "type": "vm"}]
    plan = build_plan(pols, asg, gs, [{"id": "pyxie-mine", "all": 0, "vmid": "5", "comment": "hand made"}], False)
    assert [w["action"] for w in plan["writes"]] == ["conflict"] and plan["writes"][0]["params"] == {}


def test_save_applies_tags_and_default():
    from pyxie_core.backup_policy_workflow import apply_scope, scope_of_save
    asg = [{"id": None, "policy_id": "p1", "scope_type": "cluster", "scope_value": None, "priority": 0, "policy_name": "Gold"},
           {"id": None, "policy_id": "p2", "scope_type": "tag", "scope_value": "db", "priority": 0, "policy_name": "Silver"}]
    tags, make_default, errs = scope_of_save({"tags": ["web", " db ", ""], "make_default": True})
    assert tags == ["db", "web"] and make_default and not errs
    out = apply_scope(asg, "p2", "Silver", tags, make_default)
    assert {(a["scope_type"], a["scope_value"], a["policy_id"]) for a in out} == {("cluster", None, "p2"), ("tag", "db", "p2"), ("tag", "web", "p2")}
    # dropping a tag the policy had removes its rule; tags=None leaves rules alone
    assert not [a for a in apply_scope(out, "p2", "Silver", ["web"], False) if a["scope_value"] == "db"]
    assert len(apply_scope(out, "p2", "Silver", None, False)) == len(out)
    assert scope_of_save({"tags": ["bad tag!"]})[2]
