"""One plate's absent wells must not be saved into another plate's view.

X14 gave ``view_state`` a stash of the wells a saved view named that the plate
in front of it does not hold, so a subset load followed by a save does not prune
them: ``restore`` fills ``ctx.runtime.view_state.absent_wells`` /
``.absent_solo`` and ``snapshot`` unions them back in.

Those two sets are not keyed to a data directory, and they were assigned only
*inside* ``restore`` — which does not run when the newly opened plate has no
``view_state`` section of its own. Opening plate A (a subset load), then plate
B (no saved view), then letting B save, wrote A's wells into B's
persistence.json, where B's next open selects wells it does not contain.

The sequence below is the one the app performs — restore, then the next
dataset's load, then that dataset's save — because both ends were individually
correct and the order was the defect. The assertion is on what ``snapshot``
would write, not on the stash it reads.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from well_viewer.core.plot_state import PlotState
from well_viewer.core.widget_registry import WidgetRegistry
from well_viewer.persistence import _doc, view_state


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    """Stub the seams that need the built application, and nothing else.

    ``_activate_tab`` walks the real centre-view stack; ``_snapshot_tabs``
    reads every plotting tab's combos. Neither is part of the well-selection
    round-trip under test, and both would otherwise force this into the
    full-app lane.
    """
    monkeypatch.setattr(view_state, "_activate_tab", lambda ctx, name: None)
    monkeypatch.setattr(view_state, "_has_home", lambda ctx: False)
    monkeypatch.setattr(view_state, "_snapshot_tabs", lambda ctx: {})
    monkeypatch.setattr(view_state, "_current_leaf_tab", lambda ctx: "Home")


def _make_ctx(data_dir: str, well_paths: dict):
    app = SimpleNamespace(
        _app_state=SimpleNamespace(dataset=SimpleNamespace(data_dir=data_dir))
    )
    return SimpleNamespace(
        window=app,
        notify=SimpleNamespace(set_status=lambda message: None),
        widgets=WidgetRegistry(SimpleNamespace()),
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths=dict(well_paths)),
            selection=SimpleNamespace(
                selections=[], active_solo_wells=set(), selected_wells=set(),
            ),
            plot=PlotState(),
            channel=SimpleNamespace(
                active_channel="", active_metric="", sync_val_col=lambda: None,
            ),
            export_prefs=SimpleNamespace(style_prefs=None, axis_prefs={}),
        ),
        runtime=SimpleNamespace(
            view_state=SimpleNamespace(
                absent_wells=set(), absent_solo=set(), pending={},
            ),
        ),
        coordinator=SimpleNamespace(refresh_sidebar_map=lambda: None),
    )


def test_a_plate_with_no_saved_view_does_not_inherit_the_previous_plate_s_wells(
    monkeypatch,
):
    ctx = _make_ctx("/plates/A", {"A01": 1, "A02": 2})

    # Plate A: a subset load — the saved view names two wells this load does
    # not hold, which is exactly what the stash exists for.
    view_state.restore(ctx, {
        "selected_wells": ["A01", "A02", "A03", "A04"],
        "selection_visibility": {"hidden_ids": [], "solo_wells": ["A03"]},
    })
    assert ctx.runtime.view_state.absent_wells == {"A03", "A04"}
    assert ctx.runtime.view_state.absent_solo == {"A03"}

    # Plate B: a different data directory with no ``view_state`` section, so
    # ``restore`` never runs and never reassigns the stash.
    ctx.window._app_state.dataset.data_dir = "/plates/B"
    ctx.state.dataset.well_paths = {"B01": 1, "B02": 2}
    ctx.state.selection.selected_wells = {"B01"}
    monkeypatch.setattr(_doc, "get_section", lambda c, name: None)
    view_state.load_from_data_dir(ctx)

    saved = view_state.snapshot(ctx)

    assert saved["selected_wells"] == ["B01"], (
        "plate A's wells were written into plate B's saved view: "
        f"{saved['selected_wells']}"
    )
    assert saved["selection_visibility"]["solo_wells"] == [], (
        "plate A's solo wells were written into plate B's saved view: "
        f"{saved['selection_visibility']['solo_wells']}"
    )


def test_a_plate_that_has_a_saved_view_still_keeps_its_own_absent_wells(
    monkeypatch,
):
    """The clear must not defeat X14 itself: the stash is rebuilt by the
    restore that follows it, for the plate being opened."""
    ctx = _make_ctx("/plates/A", {"A01": 1})
    ctx.runtime.view_state.absent_wells = {"Z99"}   # a previous plate's

    monkeypatch.setattr(_doc, "get_section", lambda c, name: {
        "selected_wells": ["A01", "A02"],
        "selection_visibility": {"hidden_ids": [], "solo_wells": []},
    })
    view_state.load_from_data_dir(ctx)

    assert ctx.runtime.view_state.absent_wells == {"A02"}, (
        "the opened plate's own absent wells must survive; only the previous "
        "plate's are dropped"
    )
