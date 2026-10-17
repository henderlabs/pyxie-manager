from pyxie_core.retention_workflow import describe, format_prune, parse_prune


def test_parse_and_format_round_trip():
    keep = parse_prune("keep-daily=7,keep-weekly=4,keep-monthly=6")
    assert keep == {"keep-daily": 7, "keep-weekly": 4, "keep-monthly": 6}
    assert format_prune(keep) == "keep-daily=7,keep-weekly=4,keep-monthly=6"


def test_parse_ignores_empty_and_junk():
    assert parse_prune(None) == {}
    assert parse_prune("keep-all=1") == {}
    assert parse_prune({"keep-last": "3", "keep-daily": 0, "other": 1}) == {"keep-last": 3}


def test_describe():
    assert describe({}) == "no limit set on this job"
    assert describe({"keep-daily": 7, "keep-yearly": 1}) == "keep daily 7, yearly 1"


def test_advisories_flag_short_prune_job_and_missing_gc():
    from pyxie_core.retention_workflow import advisories
    out = advisories({"keep-daily": 30, "keep-monthly": 12}, "ds1", {"gc_schedule": None}, [{"id": "p1", "store": "ds1", "keep-daily": 21, "keep-monthly": 12}])
    texts = " ".join(a["text"] for a in out)
    assert "garbage-collection" in texts and "daily 21 (policy 30)" in texts and "monthly" not in texts.split("keeps fewer:")[1]


def test_advisories_non_pbs_and_unknown():
    from pyxie_core.retention_workflow import advisories
    assert advisories({}, None, None, [])[0]["level"] == "info"
    assert "cannot read" in advisories({"keep-daily": 1}, "d", None, [])[0]["text"]
