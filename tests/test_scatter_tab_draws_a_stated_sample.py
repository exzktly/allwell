"""The Scatter tab itself caps what it draws and says so (tracker F3b).

``tests/test_scatter_draws_a_stated_sample.py`` holds the pooled collector to the
whole-selection one; this holds the tab to calling it with its cap. A collector
that can sample is worth nothing if the redraw never asks it to, and only a
redraw through the real app can show that (CLAUDE.md §7.6). Full-app: slow lane.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def test_the_tab_draws_a_stated_sample_above_its_cap(loaded_app, monkeypatch):
    from review.features.scatter import controller
    from review.features.scatter._annotations import SAMPLED_NOTE_TAG

    app = loaded_app
    assert app._notebook.setCurrentByName("Plotting")
    assert app._plotting_notebook.setCurrentByName("Scatter Plot")
    ctx = app._ctx
    ctx.state.selection.selected_wells = set(ctx.state.dataset.well_paths)
    monkeypatch.setattr(controller, "SCATTER_MAX_POINTS", 5)

    controller.redraw_scatter_orchestrate(ctx)

    ax = ctx.widgets.get_or_none("_ax_scatter")
    notes = [t.get_text() for t in ax.texts if getattr(t, SAMPLED_NOTE_TAG, False)]
    assert len(notes) == 1 and notes[0].startswith("Drawn: a sample of 5 of "), notes
    points = ctx.runtime.scatter.interaction_cache["points"]
    assert len(points) == 5, "the tab drew more than its cap"
    wells = set(ctx.state.dataset.well_paths)
    for *_xy, meta in points:
        well, _filename, _nid, row = meta
        assert well in wells and row >= 0, "a drawn point does not name a cell a click can open"
