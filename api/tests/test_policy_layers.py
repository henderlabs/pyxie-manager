from pyxie_core.policy_layers import (job_ids, plan_pbs, sync_filter, three_two_one, valid_namespace, validate_layer, validate_layers)

T = "11111111-1111-1111-1111-111111111111"
L1 = "22222222-2222-2222-2222-222222222222"


def layer(**kw):
    return {"id": L1, "label": "Offsite", "target_id": T, "store": "wasabi", "namespace": "", "schedule": "monthly", "what": "all", "last": None, "verified_only": False,
            "remove_vanished": False, "prune_schedule": "daily", "gc_schedule": None, "enabled": True, "keep_last": None, "keep_hourly": None, "keep_daily": None,
            "keep_weekly": None, "keep_monthly": None, "keep_yearly": None, **kw}


def state(**kw):
    s = {"stores": {"nas": {"gc_schedule": None}, "wasabi": {"gc_schedule": None}}, "namespaces": {"nas": [""], "wasabi": [""]}, "sync": [], "prune": [], "verify": [], "remote": "localhost"}
    s.update(kw)
    return {T: s}


SRC = {"st-pbs": {"target_id": T, "datastore": "nas"}}


def pol(pid, name, layers, **kw):
    return {"id": pid, "name": name, "storage": "st-pbs", "gc_schedule": None, "layers": layers, **kw}


def test_namespaces_and_ids():
    assert valid_namespace("") and valid_namespace("short-lived") and valid_namespace("a/b")
    assert not valid_namespace("../x") and not valid_namespace("a b") and not valid_namespace("/".join("x" * 8))
    s, p = job_ids("Short-lived", L1)
    assert s == "px-short-lived-222222-s" and p.endswith("-p") and len(s) <= 32 and len(job_ids("A" * 80, L1)[0]) <= 32


def test_validate_layer():
    c, e = validate_layer({"id": L1, "label": "Offsite", "target_id": T, "store": "wasabi", "namespace": "/pyxie/x/", "schedule": "monthly", "what": "last", "last": 3, "keep_monthly": 6})
    assert not e and c["namespace"] == "pyxie/x" and c["last"] == 3 and c["keep_monthly"] == 6 and c["prune_schedule"] == "daily"
    assert validate_layer({"label": "x", "target_id": "no", "store": "", "schedule": "", "what": "weird"})[1]
    _, e2 = validate_layers([layer(), layer(id="33333333-3333-3333-3333-333333333333")])
    assert any("same place" in x for x in e2)


def test_plan_namespace_copy_with_pruning():
    p = pol("p1", "Short", [layer(namespace="short", keep_daily=3, gc_schedule="sat 18:15")])
    r = plan_pbs([p], {"p1": [101, 102]}, {101: "lxc", 102: "qemu"}, None, {101, 102, 103}, SRC, state())
    kinds = [(w["kind"], w["action"]) for w in r["writes"]]
    assert ("namespace", "create") in kinds and ("sync", "create") in kinds and ("prune", "create") in kinds and ("gc", "update") in kinds and not r["errors"]
    sync = next(w for w in r["writes"] if w["kind"] == "sync")["params"]
    assert sync["group-filter"] == ["group:ct/101", "group:vm/102"] and sync["ns"] == "short" and sync["max-depth"] == 0 and sync["remote-store"] == "nas"
    prune = next(w for w in r["writes"] if w["kind"] == "prune")["params"]
    assert prune["ns"] == "short" and prune["keep-daily"] == 3 and prune["max-depth"] == 0


def test_default_policy_copies_everyone_minus_the_others():
    assert sync_filter(True, [101], {102: "qemu"}, {101, 102}) == ["exclude:group:vm/102"]
    assert sync_filter(False, [101, 102], {101: "lxc"}, {101, 102, 103}) == ["group:ct/101", "group:vm/102"]
    p = pol("d", "Standard", [layer()])
    r = plan_pbs([p], {"d": [101]}, {}, "d", {101, 102}, SRC, state())
    sync = next(w for w in r["writes"] if w["kind"] == "sync")["params"]
    assert sync["group-filter"] == ["exclude:group:vm/102"] and "ns" not in sync


def test_up_to_date_and_orphans_and_errors():
    p = pol("p1", "Short", [layer(namespace="short", keep_daily=3)])
    first = plan_pbs([p], {"p1": [101]}, {}, None, {101}, SRC, state())
    live_sync = {**next(w for w in first["writes"] if w["kind"] == "sync")["params"]}
    live_prune = {**next(w for w in first["writes"] if w["kind"] == "prune")["params"]}
    st = state(sync=[live_sync], prune=[live_prune], namespaces={"nas": [""], "wasabi": ["", "short"]})
    assert plan_pbs([p], {"p1": [101]}, {}, None, {101}, SRC, st)["writes"] == []
    gone = plan_pbs([], {}, {}, None, {101}, SRC, st)
    assert {(w["kind"], w["action"]) for w in gone["writes"]} == {("sync", "delete"), ("prune", "delete")}
    bad = plan_pbs([p], {"p1": [101]}, {}, None, {101}, SRC, state(remote=None))
    assert bad["errors"] and not bad["writes"]
    same = plan_pbs([pol("p1", "S", [layer(store="nas")])], {"p1": [101]}, {}, None, {101}, SRC, state())
    assert any("same place" in e for e in same["errors"])


def test_unmanaged_job_gets_excludes_and_root_prune_is_limited():
    other = {"id": "monthly-offsite", "store": "wasabi", "remote": "localhost", "remote-store": "nas", "schedule": "monthly"}
    root_prune = {"id": "prune-wasabi", "store": "wasabi", "keep-monthly": 6, "schedule": "monthly"}
    p = pol("p1", "Short", [layer(namespace="short", keep_daily=3)])
    r = plan_pbs([p], {"p1": [101]}, {101: "qemu"}, None, {101}, SRC, state(sync=[other], prune=[root_prune]))
    assert r["others"] == [{"target_id": T, "id": "monthly-offsite", "store": "wasabi", "add_filters": ["exclude:group:vm/101"], "vmids": [101]}]
    assert any(w["id"] == "prune-wasabi" and w["params"] == {"max-depth": 0} for w in r["writes"])


def test_gc_conflict_and_321():
    a = pol("p1", "A", [layer(gc_schedule="daily")])
    b = pol("p2", "B", [layer(id="33333333-3333-3333-3333-333333333333", namespace="x", gc_schedule="weekly")])
    assert any("different garbage-collection" in e for e in plan_pbs([a, b], {"p1": [1], "p2": [2]}, {}, None, {1, 2}, SRC, state())["errors"])
    no_off = three_two_one([{"backend": "filesystem", "offsite": False}, {"backend": "filesystem", "offsite": False}])
    assert no_off["ok"] is False and no_off["copies"] == 3 and no_off["missing"] == ["an offsite copy"]
    assert three_two_one([{"backend": "filesystem", "offsite": False}])["missing"][0].startswith("1 more copy")
    ok = three_two_one([{"backend": "filesystem", "offsite": False}, {"backend": "s3", "offsite": True}])
    assert ok["ok"] and ok["copies"] == 3


def test_local_remote_choice_does_not_depend_on_order():
    from pyxie_core.pbs_state import pick_local_remote
    a = {"name": "localhost", "host": "127.0.0.1"}
    b = {"name": "old-pbs", "host": "192.168.111.105"}
    assert pick_local_remote([a, b], "192.168.111.105") == "localhost" == pick_local_remote([b, a], "192.168.111.105")
    assert pick_local_remote([b], "192.168.111.105") == "old-pbs" and pick_local_remote([{"name": "x", "host": "10.0.0.1"}], "192.168.111.105") is None


def test_maintenance_switches_for_the_default_plan():
    from pyxie_core.policy_layers import validate_maintenance
    m, e = validate_maintenance({"prune": "daily", "gc": "sat 18:15", "verify": "sun 06:00", "verify_after": 30})
    assert not e and m["gc"] == "sat 18:15" and m["verify_after"] == 30
    assert validate_maintenance({"gc": "$$$"})[1] and validate_maintenance({"verify_after": 0})[1] and validate_maintenance(None)[0]["gc"] is None
    p = pol("d", "Standard", [layer(keep_monthly=6)], keep_daily=14, keep_weekly=4, maintenance=m)
    root_prune_nas = {"id": "prune-archive", "store": "nas", "keep-daily": 21, "keep-weekly": 8, "keep-monthly": 6, "schedule": "05:00"}
    root_prune_w = {"id": "prune-wasabi", "store": "wasabi", "keep-monthly": 6, "schedule": "monthly"}
    st = state(prune=[root_prune_nas, root_prune_w])
    r = plan_pbs([p], {"d": [101]}, {}, "d", {101, 102}, SRC, st)
    assert not r["errors"]
    by = {(w["kind"], w["id"]): w for w in r["writes"]}
    nas = by[("prune", "prune-archive")]
    assert nas["params"] == {"keep-daily": 14, "keep-weekly": 4} and nas["clear"] == ["keep-monthly"]  # the rest is pruned
    assert ("prune", "prune-wasabi") not in by  # already keeps 6 monthly
    assert {w["store"] for w in r["writes"] if w["kind"] == "verify"} == {"nas", "wasabi"}  # verification of every place the plan writes to
    assert {w["store"] for w in r["writes"] if w["kind"] == "gc"} == {"nas", "wasabi"}
    # an existing verify job on the datastore root is respected; a plan that is not the default never touches the root prune job
    st2 = state(prune=[root_prune_nas], verify=[{"id": "verify-nas", "store": "nas", "schedule": "sun 06:00"}])
    r2 = plan_pbs([p], {"d": [101]}, {}, "d", {101}, SRC, st2)
    assert not [w for w in r2["writes"] if w["kind"] == "verify" and w["store"] == "nas"]
    other = pol("o", "Other", [], keep_daily=3, maintenance=m)
    r3 = plan_pbs([other], {"o": [101]}, {}, None, {101}, SRC, st2)
    assert not [w for w in r3["writes"] if w["kind"] == "prune"]
    # a plan with no prune job at the root gets its own; nothing changes once it matches
    r4 = plan_pbs([p], {"d": [101]}, {}, "d", {101}, SRC, state())
    mine = next(w for w in r4["writes"] if w["kind"] == "prune" and w["store"] == "nas")
    assert mine["action"] == "create" and mine["params"]["keep-daily"] == 14 and mine["params"]["schedule"] == "daily"
