"""A size-class edit rewrites every held well or none (``population_gate.apply_population_to_cache``).

The report: editing the size-class bounds on a nanowell plate threw memory errors. The rewrite walked the held
wells one at a time, so running out of memory part-way left the wells before the failure under the new bounds and
the wells after it under the old — and because the class columns are named by the classes' labels, not their
bounds, the two looked identical on every plot while the panel showed the new bounds.

These tests drive the edit through the caller the panel uses (``qc.controller.set_size_classes``) on a real loaded
dataset, fail the write part-way, and read every well back the way a plot does. The bounds are moved without
renaming a class, so the old and the new values sit under the same column name — the case the defect was made of.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from review.domain.size_classes import SizeClassRule, share_column

OLD = SizeClassRule.of(("Large", 245, None), ("Small", None, 225))
NEW = SizeClassRule.of(("Large", 228, None), ("Small", None, 227))     # same labels, so the same columns


@pytest.fixture
def shell(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.mini_dataset import build_mini_dataset

    root = build_mini_dataset(tmp_path / "mini")
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root)
    ctx.state.selection.selected_wells = {"A01"}
    return ctx


def _shares(ctx, rule) -> dict[str, list]:
    """Each well's *Large* share per cell, read the way a plot reads it (``get_rows``), beside what *rule* gives."""
    from review.data.data_loading import get_rows
    from review.data.population_gate import _plan

    out = {}
    for well in sorted(ctx.state.dataset.well_paths):
        df = get_rows(ctx, well)
        _stale, expected = _plan(df, None, rule)
        out[well] = (list(np.nan_to_num(df[share_column("Large")].to_numpy(), nan=-1)),
                     list(np.nan_to_num(expected[share_column("Large")], nan=-1)))
    return out


def _start(shell, qtbot):
    from review.features.qc.controller import get_qc_params
    from tests._panel_jobs import apply_qc_params

    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=OLD))
    held = shell.state.dataset.cache.keys()
    assert len(held) >= 3, f"the fixture must hold several wells for a part-way failure to mean anything: {held}"
    # The fixture must make the two rules disagree on the wells after the first, or a well left under the old rule
    # would read exactly like one rewritten — the assertion below could not tell them apart.
    from review.data.population_gate import _plan

    disagree = [w for w in held[1:]
                if not np.array_equal(np.nan_to_num(_plan(shell.state.dataset.cache[w], None, OLD)[1][share_column("Large")]),
                                      np.nan_to_num(_plan(shell.state.dataset.cache[w], None, NEW)[1][share_column("Large")]))]
    assert disagree, "the old and the new bounds class every cell of the later wells alike"
    return held


def _fail_on_the_second_write(monkeypatch, exc):
    from review.data import population_gate as pg

    real, calls = pg._commit, {"n": 0}

    def flaky(df, plan):
        calls["n"] += 1
        if calls["n"] == 2:
            raise exc
        return real(df, plan)

    monkeypatch.setattr(pg, "_commit", flaky)
    return calls


def test_running_out_of_memory_part_way_leaves_every_well_on_the_new_bounds(shell, qtbot, monkeypatch):
    from review.features.qc.controller import get_qc_params, only_size_classes_differ, set_size_classes

    _start(shell, qtbot)
    statuses: list[str] = []
    monkeypatch.setattr(shell.notify, "set_status", statuses.append)
    calls = _fail_on_the_second_write(monkeypatch, MemoryError("simulated"))
    params = replace(get_qc_params(shell), size_classes=NEW)
    assert only_size_classes_differ(params, get_qc_params(shell))         # the panel's light path

    set_size_classes(shell, params)                                         # does not raise

    assert calls["n"] == 2, "the failure was not reached: the test measured nothing"
    monkeypatch.undo()                                                      # reads below rewrite wells for real
    got = _shares(shell, NEW)
    wrong = [well for well, (have, want) in got.items() if have != want]
    assert wrong == [], f"wells left under the old bounds after the failed rewrite: {wrong}"
    assert any(s.startswith("Out of memory while applying the size classes") for s in statuses), statuses
    assert shell.state.dataset.released_wells == []                        # reported, so taken


def test_a_clean_edit_drops_nothing_and_says_nothing(shell, qtbot, monkeypatch):
    from review.features.qc.controller import get_qc_params, set_size_classes

    held = _start(shell, qtbot)
    statuses: list[str] = []
    monkeypatch.setattr(shell.notify, "set_status", statuses.append)
    set_size_classes(shell, replace(get_qc_params(shell), size_classes=NEW))
    assert shell.state.dataset.cache.keys() == held
    assert not any("Out of memory" in s for s in statuses)
    assert all(have == want for have, want in _shares(shell, NEW).values())


def test_a_failure_that_is_not_memory_still_leaves_one_rule_and_is_raised(shell, qtbot, monkeypatch):
    from review.data.population_gate import apply_population_to_cache
    from review.features.qc.controller import get_qc_params

    _start(shell, qtbot)
    shell.state.qc.params = replace(get_qc_params(shell), size_classes=NEW)
    _fail_on_the_second_write(monkeypatch, ValueError("a defect"))
    with pytest.raises(ValueError, match="a defect"):
        apply_population_to_cache(shell)
    monkeypatch.undo()
    assert all(have == want for have, want in _shares(shell, NEW).values())


def test_the_wells_dropped_by_a_re_projection_on_the_worker_are_reported_on_the_window_s_thread(shell, monkeypatch):
    """With a class chosen, an edit takes the full re-projection, which runs the rewrite on a worker and finishes in
    ``finish_qc_recompute`` — the place it must be said."""
    from review.features.qc.controller import finish_qc_recompute

    statuses: list[str] = []
    monkeypatch.setattr(shell.notify, "set_status", statuses.append)
    shell.state.dataset.released_wells = ["A02", "B01"]
    finish_qc_recompute(shell, [])
    assert [s for s in statuses if "Out of memory" in s] == [
        "Out of memory while applying the size classes, so the 2 well(s) held in memory were dropped and are read "
        "back under the size classes now shown when next drawn. Every plot uses those classes; drawing is slower "
        "until the wells are back. Closing other programs leaves more room."]
    assert shell.state.dataset.released_wells == []


def test_a_failed_rewrite_moves_the_rows_version_so_no_memo_of_the_old_rows_is_served(shell, qtbot, monkeypatch):
    """A well read back after the release differs from the one dropped, unlike after an ordinary eviction, so every
    memo keyed on ``rows_version`` (the per-well aggregates, ``plate_scan``) must decline."""
    from review.data.population_gate import apply_population_to_cache
    from review.features.qc.controller import get_qc_params

    _start(shell, qtbot)
    shell.state.qc.params = replace(get_qc_params(shell), size_classes=NEW)
    before = shell.state.dataset.rows_version
    _fail_on_the_second_write(monkeypatch, MemoryError("simulated"))
    assert apply_population_to_cache(shell) == sorted(shell.state.dataset.well_paths)
    assert shell.state.dataset.rows_version == before + 1


def test_a_well_kept_by_another_thread_is_rewritten_after_the_others_are_dropped(shell, qtbot, monkeypatch):
    """``RowCache.release`` keeps a pinned frame — one ``load_and_project`` is working on — and the failure may have
    been part-way through that very frame. The failure here is A02's own write, with A02 pinned."""
    from review.data.population_gate import apply_population_to_cache
    from review.features.qc.controller import get_qc_params

    held = _start(shell, qtbot)
    assert held[1] == "A02"
    shell.state.qc.params = replace(get_qc_params(shell), size_classes=NEW)
    cache = shell.state.dataset.cache
    _fail_on_the_second_write(monkeypatch, MemoryError("simulated"))
    with cache.pinned("A02"):
        apply_population_to_cache(shell)
        assert cache.keys() == ["A02"]                              # every other well dropped
        df = cache["A02"]
        from review.data.population_gate import _plan

        _stale, want = _plan(df, None, NEW)
        assert np.array_equal(np.nan_to_num(df[share_column("Large")].to_numpy(), nan=-1),
                              np.nan_to_num(want[share_column("Large")], nan=-1))
