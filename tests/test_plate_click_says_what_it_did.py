"""A click on a well of a shown group hides the whole group — say so, and mark the hidden wells on the plate.

Reported on the Nanowells page: clicking the control's and the treatment's wells to "choose" them hid both, and
nothing said a click had done anything but empty the graphs. Driven on the real app through the plate's own
``wellActivated`` signal, asserting at the boundary: the toast the user reads and the glyphs the plate paints.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _on_bar_plots(app):
    app._notebook.setCurrentByName("Plotting")
    app._plotting_notebook.setCurrentByName("Bar Plots")
    app._refresh_sidebar_map_now()


def test_a_plate_click_that_hides_a_group_says_so_and_marks_its_wells(loaded_app, monkeypatch):
    from review.core import selection_controller as sc

    app = loaded_app
    wells = sorted(app._app_state.dataset.well_paths)[:2]
    sc.sel_add(app._ctx, "G1", wells, make_current=False)
    _on_bar_plots(app)
    said = []
    monkeypatch.setattr(app._ctx.notify, "toast", lambda message, **_kw: said.append(message))
    markers = app._sidebar_plate._grid._markers

    app._sidebar_plate.wellActivated.emit(wells[0])
    app._refresh_sidebar_map_now()
    assert said == [f"G1 hidden ({', '.join(wells)}): click one of its wells again to show it."]
    marked = {k for k, glyph in markers.items() if glyph == sc.HIDDEN_GLYPH}
    assert len(marked) == len(wells)                     # exactly the group's wells, nothing else

    app._sidebar_plate.wellActivated.emit(wells[1])
    app._refresh_sidebar_map_now()
    assert said[-1] == f"G1 shown ({', '.join(wells)})."
    assert not any(glyph == sc.HIDDEN_GLYPH for glyph in markers.values())
