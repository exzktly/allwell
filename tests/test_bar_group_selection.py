"""Bar-plot key selection in group (replicate-set) mode.

Regression: after "Deselect all" hid every group, ``ordered_bar_keys`` fell back
to the stale plate ``selected_wells`` and resurrected every well as a bar. In
group mode the plate is passive, so that fallback is wrong — no visible group
means no bars.

These import Qt-pulling feature modules but exercise pure logic; they run in
CI's lane that has PySide6/matplotlib available.
"""

from __future__ import annotations

from types import SimpleNamespace


def _app(*, rsets, selections, selected, well_paths, bar_order=None):
    state = SimpleNamespace(
        selection=SimpleNamespace(selections=selections, selected_wells=selected),
        dataset=SimpleNamespace(well_paths={w: None for w in well_paths}),
        plot=SimpleNamespace(bar_order=bar_order),
    )
    app = SimpleNamespace(
        _app_state=state,
        _rep_sets_active=lambda: rsets,
        _parse_rc=lambda w: (w[:1], w[1:]),
    )
    from well_viewer.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state,
                               runtime=AppRuntime(app))
    return app


def test_group_mode_all_hidden_yields_no_bars():
    from well_viewer.features.barplot.controller import ordered_bar_keys

    # Groups defined but none visible (all hidden) → rep_sets_active() == [].
    app = _app(rsets=[], selections=[{"name": "g1", "hidden": True}],
               selected=["A1", "B2"], well_paths=["A1", "B2"])
    assert ordered_bar_keys(app._ctx) == []          # NOT ["A1", "B2"]


def test_visible_groups_drive_keys():
    from well_viewer.features.barplot.controller import ordered_bar_keys

    app = _app(rsets=[SimpleNamespace(name="g1"), SimpleNamespace(name="g2")],
               selections=[{"name": "g1", "wells": ["A1"]}, {"name": "g2", "wells": ["B2"]}],
               selected=["A1"], well_paths=["A1", "B2"])
    assert ordered_bar_keys(app._ctx) == ["g1", "g2"]


def test_no_groups_falls_back_to_selected_wells():
    from well_viewer.features.barplot.controller import ordered_bar_keys

    # No selections defined → plain plate selection drives the bars.
    app = _app(rsets=[], selections=[], selected=["B2", "A1"],
               well_paths=["A1", "B2"])
    assert ordered_bar_keys(app._ctx) == ["A1", "B2"]   # sorted by _parse_rc
