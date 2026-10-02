"""Affinity must see moves already decided earlier in the SAME plan.

Reproduces CRE-QADB2 + CRE-StgDB2 (hard keep-apart) both being planned onto empty m401.
Pure logic: no database."""

from types import SimpleNamespace
from uuid import uuid4

from pyxie_core.placement import PLANNED_KEY, note_planned_move, residents_with_planned


def _wl(**kw):
    return SimpleNamespace(id=uuid4(), memory_bytes=kw.get("mem", 100), **{k: v for k, v in kw.items() if k != "mem"})


def test_note_planned_move_tracks_memory_and_placement():
    sim, a = {}, _wl(mem=128)
    note_planned_move(sim, a, "m401")
    assert sim["m401"] == 128
    assert sim[PLANNED_KEY] == {str(a.id): "m401"}
    b = _wl(mem=64)
    note_planned_move(sim, b, "m401")
    assert sim["m401"] == 192 and len(sim[PLANNED_KEY]) == 2


def test_partner_planned_onto_candidate_counts_as_resident():
    db1, db2 = _wl(), _wl()
    sim = {}
    note_planned_move(sim, db1, "m401")
    # while choosing db2's host, db1 now counts as already on m401
    assert str(db1.id) in residents_with_planned(set(), sim[PLANNED_KEY], "m401")
    assert str(db1.id) not in residents_with_planned(set(), sim[PLANNED_KEY], "m405")


def test_partner_planned_away_no_longer_counts_where_it_is_today():
    db1 = _wl()
    sim = {}
    note_planned_move(sim, db1, "m405")
    # db1 sits on m403 today but is planned to leave it
    assert str(db1.id) not in residents_with_planned({str(db1.id), "other"}, sim[PLANNED_KEY], "m403")
    assert "other" in residents_with_planned({str(db1.id), "other"}, sim[PLANNED_KEY], "m403")


def test_no_plan_changes_nothing():
    assert residents_with_planned({"a"}, None, "m1") == {"a"}
    assert residents_with_planned({"a"}, {}, "m1") == {"a"}
