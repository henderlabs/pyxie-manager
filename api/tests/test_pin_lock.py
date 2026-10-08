"""A guest sitting on its pinned host (or marked Do not move) is never considered by Balance Load. Pure logic."""

from types import SimpleNamespace

from pyxie_core.recommendations import left_alone_reason


def wl(node, preferred=None, dnm=False):
    return SimpleNamespace(node_id=node, preferred_node_id=preferred, do_not_move=dnm)


def test_guest_on_its_pin_is_left_alone():
    assert left_alone_reason(wl("a", preferred="a")) == "pinned"


def test_guest_away_from_its_pin_may_move_toward_it():
    assert left_alone_reason(wl("b", preferred="a")) is None


def test_unpinned_guest_is_free_to_move():
    assert left_alone_reason(wl("a")) is None


def test_do_not_move_wins_and_is_reported_as_such():
    assert left_alone_reason(wl("a", preferred="a", dnm=True)) == "do_not_move"
    assert left_alone_reason(wl("a", dnm=True)) == "do_not_move"
