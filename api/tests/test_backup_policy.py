from pyxie_core.backup_policy import (covers, desired_membership, job_id_for, job_params, leave_other_jobs, parse_retention, plan_job_writes, resolve_assignments, retention_string,
                                      describe_retention, validate_policy)


def good(**kw):
    base = {"name": "Gold", "storage": "st-pbs", "schedule": "02:00", "mode": "snapshot", "keep_last": 3, "keep_daily": 7}
    base.update(kw)
    return base


def test_validate():
    clean, errs = validate_policy(good())
    assert not errs and clean["keep_daily"] == 7 and clean["keep_weekly"] is None
    for bad in (good(name=""), good(storage=""), good(schedule="whenever"), good(schedule="sat"), good(mode="fast"), good(keep_last=0 + 100000), good(keep_daily="x"), good(notes_template="a\nb")):
        assert validate_policy(bad)[1], bad
    assert not validate_policy(good(schedule="mon..fri 22:30"))[1] and not validate_policy(good(schedule="*-*-01 02:00"))[1]
    assert not validate_policy(good(keep_last=None, keep_daily=None))[1]  # keep everything is allowed


def test_retention():
    c, _ = validate_policy(good(keep_weekly=4))
    assert retention_string(c) == "keep-last=3,keep-daily=7,keep-weekly=4"
    assert parse_retention("keep-last=3,keep-daily=7") == {"keep_last": 3, "keep_daily": 7}
    assert retention_string(validate_policy(good(keep_last=None, keep_daily=None))[0]) is None
    assert describe_retention(c).startswith("keep 3 last")


def test_job_id_and_params():
    assert job_id_for("Gold Tier!") == "pyxie-gold-tier"
    c, _ = validate_policy(good())
    p = job_params({**c, "id": "x"}, [102, 101, 101])
    assert p["vmid"] == "101,102" and p["all"] == 0 and p["prune-backups"] == "keep-last=3,keep-daily=7" and p["enabled"] == 1 and p["id"] == "pyxie-gold"
    assert "prune-backups" not in job_params({**validate_policy(good(keep_last=None, keep_daily=None))[0], "id": "x"}, [1])


def test_resolution_order():
    ws = [{"id": "a", "tags": ["db", "prod"]}, {"id": "b", "tags": ["prod"]}, {"id": "c", "tags": []}, {"id": "d", "tags": ["db"]}]
    asg = [
        {"policy_id": "P_all", "scope_type": "cluster", "scope_value": None, "priority": 0, "policy_name": "All"},
        {"policy_id": "P_prod", "scope_type": "tag", "scope_value": "prod", "priority": 0, "policy_name": "Prod"},
        {"policy_id": "P_db", "scope_type": "tag", "scope_value": "db", "priority": 5, "policy_name": "Db"},
        {"policy_id": None, "scope_type": "vm", "scope_value": "d", "priority": 0, "policy_name": ""},
    ]
    r = resolve_assignments(ws, asg)
    assert r["a"]["policy_id"] == "P_db" and r["b"]["policy_id"] == "P_prod" and r["c"]["policy_id"] == "P_all" and r["d"]["policy_id"] is None
    assert r["d"]["source"] == "set for this guest" and r["c"]["source"] == "the default policy"
    assert desired_membership(r, {"a": 101, "b": 102, "c": 103, "d": 104}) == {"P_db": [101], "P_prod": [102], "P_all": [103]}


def test_plan_writes():
    pol = [{"id": "p1", "name": "Gold", **validate_policy(good())[0]}, {"id": "p2", "name": "Empty", **validate_policy(good(name="Empty"))[0]}]
    jobs = [{"id": "pyxie-gold", "storage": "st-pbs", "schedule": "02:00", "mode": "snapshot", "enabled": 1, "all": 0, "vmid": "101", "prune-backups": "keep-last=3,keep-daily=7", "comment": "PyXie policy: Gold"},
            {"id": "pyxie-empty", "storage": "st-pbs", "schedule": "02:00", "mode": "snapshot", "enabled": 1, "all": 0, "vmid": "5", "comment": "PyXie policy: Empty"},
            {"id": "pyxie-orphan", "all": 0, "vmid": "9", "comment": "PyXie policy: Gone"}, {"id": "pyxie-handmade", "all": 0, "vmid": "7", "comment": "my own job"}, {"id": "nightly", "all": 1}]
    w = plan_job_writes(pol, {"p1": [101, 102]}, jobs)
    assert [(x["action"], x["job_id"]) for x in w] == [("update", "pyxie-gold"), ("delete", "pyxie-empty"), ("delete", "pyxie-orphan")]
    assert "VM list" in w[0]["why"]
    assert plan_job_writes(pol, {"p1": [101]}, jobs[:1]) == []  # already matches (the job is ours: its comment says so)
    assert plan_job_writes(pol, {"p1": [101]}, [])[0]["action"] == "create"


def test_other_jobs():
    jobs = [{"id": "nightly", "all": 1, "exclude": "103"}, {"id": "weekly", "all": 0, "vmid": "101,104"}, {"id": "pyxie-gold", "all": 0, "vmid": "101", "comment": "PyXie policy: Gold"}]
    assert covers(jobs[0], 101, {101, 103}) and not covers(jobs[0], 103, {101, 103}) and covers(jobs[1], 104, set())
    out = leave_other_jobs(jobs, {101, 103}, {101, 103, 104})
    assert out == [{"job_id": "nightly", "action": "exclude", "vmids": [101]}, {"job_id": "weekly", "action": "remove", "vmids": [101]}]


def test_retention_as_proxmox_lists_it():
    assert parse_retention({"keep-daily": "3", "keep-last": "2"}) == {"keep_daily": 3, "keep_last": 2}
    job = {"id": "pyxie-gold", "storage": "st-pbs", "schedule": "02:00", "mode": "snapshot", "enabled": 1, "all": 0, "vmid": "101", "prune-backups": {"keep-daily": "7", "keep-last": "3"}, "comment": "PyXie policy: Gold"}
    pol = [{"id": "p1", "name": "Gold", **validate_policy(good())[0]}]
    assert plan_job_writes(pol, {"p1": [101]}, [job]) == []


def test_hand_made_pyxie_named_jobs_are_never_touched():
    pol = [{"id": "p1", "name": "Handmade", **validate_policy(good(name="Handmade"))[0]}]
    jobs = [{"id": "pyxie-handmade", "all": 0, "vmid": "7", "comment": "my own job"}, {"id": "pyxie-offsite", "all": 0, "vmid": "8"}]
    w = plan_job_writes(pol, {"p1": [101]}, jobs)
    assert [(x["action"], x["job_id"]) for x in w] == [("conflict", "pyxie-handmade")]  # the name clash is reported, the other hand-made job is left alone
    assert plan_job_writes([], {}, jobs) == []


def test_other_job_that_would_be_left_empty_is_flagged():
    jobs = [{"id": "weekly", "all": 0, "vmid": "101"}, {"id": "two", "all": 0, "vmid": "101,102"}]
    out = leave_other_jobs(jobs, {101}, {101, 102})
    assert out[0]["leaves_empty"] is True and "leaves_empty" not in out[1]


def test_tags_match_case_insensitively():
    r = resolve_assignments([{"id": "a", "tags": ["prod"]}], [{"policy_id": "P", "scope_type": "tag", "scope_value": "Prod", "priority": 0, "policy_name": "P"}])
    assert r["a"]["policy_id"] == "P"


def test_default_policy_is_an_all_guests_job_minus_the_others():
    pol = [{"id": "d", "name": "Standard", "storage": "st-pbs", "schedule": "02:00", "mode": "snapshot", "enabled": True, "job_id": "pyxie-standard", "keep_daily": 7}]
    w = plan_job_writes(pol, {"d": [101, 102]}, [], "d", {101, 102, 103, 104})
    assert w[0]["action"] == "create" and w[0]["params"]["all"] == 1 and w[0]["params"]["exclude"] == "103,104" and "vmid" not in w[0]["params"]
    # a new guest (105) joins the default job on its own: nothing to change
    live = [{**w[0]["params"], "type": "vzdump"}]
    assert plan_job_writes(pol, {"d": [101, 102, 105]}, live, "d", {101, 102, 103, 104, 105}) == []
    assert plan_job_writes(pol, {"d": [101, 102]}, live, "d", {101, 102, 103, 104}) == []
