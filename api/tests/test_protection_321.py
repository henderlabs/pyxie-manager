import time
from pyxie_core import protection_321 as p

NOW = 1_800_000_000
NAS = {"name": "2-NAS", "backend": "filesystem"}
OFF = {"name": "3-OFFSITE_Wasabi", "backend": "s3"}


def test_full_3_2_1_is_ok():
    r = p.score_guest({"2-NAS": NOW - 3600, "3-OFFSITE_Wasabi": NOW - 86400}, [NAS, OFF], ["nightly"], NOW)
    assert r["status"] == "ok" and r["copy_count"] == 3 and r["offsite"] and not r["problems"]


def test_stale_offsite_is_partial_and_named():
    r = p.score_guest({"2-NAS": NOW - 3600, "3-OFFSITE_Wasabi": NOW - 15 * 86400}, [NAS, OFF], ["nightly"], NOW)
    assert r["status"] == "partial" and not r["offsite"]
    assert any("(offsite): newest backup is 15d old, should be under" in x for x in r["problems"])


def test_no_job_no_backup():
    r = p.score_guest({}, [NAS, OFF], [], NOW)
    assert r["status"] == "none" and "No backup job includes it" in r["problems"]


def test_offsite_by_name_counts():
    assert p.is_offsite({"name": "my-offsite", "backend": "filesystem"})
    assert not p.is_offsite(NAS)


def test_store_notes_offsite_stale_no_sync():
    n = p.store_notes(dict(OFF, newest=NOW - 15 * 86400), [], [], [], NOW - 15 * 86400, NOW)
    texts = " ".join(x["text"] for x in n)
    assert "15d ago, later than its schedule allows" in texts and "No verify job" in texts and "No prune job" in texts


def test_retention_text():
    assert p.describe_retention({"keep-daily": 21, "keep-weekly": 8}) == "keep daily 21, weekly 8"
    assert p.describe_retention({}) == "keep all"
