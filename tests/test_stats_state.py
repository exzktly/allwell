"""Pure-Python unit tests for ``StatsState``.

No Qt, no app — these exercise the statistics-group management directly: add /
select / delete / rename / clear and the active-group cursor clamping.
"""

from __future__ import annotations

from review.features.stats.state import StatsState


def test_defaults():
    ss = StatsState()
    assert ss.groups == []
    assert ss.active_grp == -1
    assert ss.active_group() is None


def test_add_group_appends_and_activates():
    ss = StatsState()
    g1 = ss.add_group()
    g2 = ss.add_group()
    assert [g.name for g in ss.groups] == ["Group 1", "Group 2"]
    assert ss.active_grp == 1
    assert ss.active_group() is g2
    assert g1 is not g2


def test_select_group():
    ss = StatsState()
    ss.add_group()
    ss.add_group()
    ss.select_group(0)
    assert ss.active_group().name == "Group 1"


def test_active_group_none_when_out_of_range():
    ss = StatsState(groups=[], active_grp=0)
    assert ss.active_group() is None


def test_delete_group():
    ss = StatsState()
    ss.add_group()
    ss.add_group()
    assert ss.delete_group(0) is True
    assert [g.name for g in ss.groups] == ["Group 2"]


def test_delete_group_clamps_active_cursor():
    ss = StatsState()
    ss.add_group()
    ss.add_group()  # active_grp == 1
    ss.delete_group(1)
    assert ss.active_grp == 0  # clamped into range


def test_delete_group_out_of_range_is_noop():
    ss = StatsState()
    ss.add_group()
    assert ss.delete_group(5) is False
    assert len(ss.groups) == 1


def test_rename_group():
    ss = StatsState()
    ss.add_group()
    assert ss.rename_group(0, "Treated") is True
    assert ss.groups[0].name == "Treated"
    assert ss.rename_group(9, "X") is False


def test_clear_groups():
    ss = StatsState()
    ss.add_group()
    ss.add_group()
    ss.clear_groups()
    assert ss.groups == []
    assert ss.active_grp == -1


def test_set_groups():
    ss = StatsState()
    g = ss.add_group()
    ss.set_groups([g])
    assert ss.groups == [g]
    assert ss.active_grp == 0


def test_set_groups_empty_resets_cursor():
    ss = StatsState(active_grp=3)
    ss.set_groups([])
    assert ss.active_grp == -1


def test_stats_cache_invalidate():
    # The group-statistics result cache moved off StatsState into the core
    # StatsCacheState slice (so plotting works without the Statistics feature).
    from review.core.stats_cache_state import StatsCacheState
    sc = StatsCacheState(cache={"k": 1})
    sc.invalidate()
    assert sc.cache == {}
