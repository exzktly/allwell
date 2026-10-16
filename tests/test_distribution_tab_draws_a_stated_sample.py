"""The Distribution tab itself draws from a stated sample above its cap (tracker F3c).

``tests/test_distribution_draws_a_stated_sample.py`` holds the pooled path to the
whole one; this holds the tab's redraw to taking it and saying so, which only a
redraw through the real app can show. Full-app: slow lane.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def test_the_tab_notes_its_sample_above_the_cap(loaded_app, monkeypatch):
    from review.features.distribution import _pooled
    from review.features.distribution import controller as C

    app = loaded_app
    assert app._notebook.setCurrentByName("Plotting")
    assert app._plotting_notebook.setCurrentByName("Distribution")
    ctx = app._ctx
    ctx.state.selection.selected_wells = set(ctx.state.dataset.well_paths)
    monkeypatch.setattr(_pooled, "DIST_MAX_VALUES", 5)

    C.redraw_distribution(ctx)

    fig = ctx.widgets.get_or_none("_distribution_fig")
    notes = [t.get_text() for ax in fig.axes for t in ax.texts if getattr(t, C.SAMPLED_NOTE_TAG, False)]
    assert len(notes) == 1 and notes[0].startswith("Curves drawn from a sample of 5 of "), notes
    title = fig.axes[0].get_title() or (fig._suptitle.get_text() if fig._suptitle else "")
    n_cells = int(title.split("(")[-1].split(" cells")[0])
    assert n_cells > 5, f"the title counts the sample, not every value: {title!r}"
