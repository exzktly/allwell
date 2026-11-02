"""Back and Forward's rules (``core/nav_history.py``) — a browser's."""

from __future__ import annotations

from core.nav_history import NavHistory


def test_back_and_forward_walk_the_visits_and_stop_at_the_ends():
    h = NavHistory()
    assert not h.can_back() and not h.can_forward()
    for place in ("a", "b", "c"):
        h.visit(place)
    assert h.can_back() and not h.can_forward()
    assert h.back() == "b" and h.back() == "a"
    assert h.back() is None and not h.can_back()
    assert h.forward() == "b" and h.forward() == "c"
    assert h.forward() is None and not h.can_forward()


def test_a_new_visit_after_back_forgets_the_way_forward():
    h = NavHistory()
    for place in ("a", "b", "c"):
        h.visit(place)
    h.back()
    h.back()
    h.visit("d")
    assert h.entries() == ["a", "d"] and not h.can_forward()


def test_the_same_place_twice_is_one_entry_and_going_there_records_nothing():
    h = NavHistory()
    h.visit("a")
    h.visit("a")
    h.visit("b")
    assert h.entries() == ["a", "b"]
    place = h.back()
    with h.replaying():
        assert not h.visit(place)        # arriving where Back sent us
        assert not h.visit("z")
    assert h.entries() == ["a", "b"] and h.can_forward()


def test_listeners_hear_each_change_and_the_oldest_go_first():
    h = NavHistory(max_entries=3)
    heard = []
    h.add_listener(lambda: heard.append((h.can_back(), h.can_forward())))
    for place in "abcd":
        h.visit(place)
    assert h.entries() == ["b", "c", "d"]
    h.back()
    assert heard[-1] == (True, True)
