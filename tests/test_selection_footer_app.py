"""The rail's selection footer in the real app: its count follows the plate,
and **All** is offered only where the plate holds more than one well.

The fast half (``tests/test_selection_footer.py``) builds each picker alone. This
half needs the shell, because what the footer says is written by
``selection_controller.refresh_sidebar_map_now`` on every repaint, and whether
**All** shows depends on which page is on screen.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _footer(app):
    from widgets.selection_footer import SelectionFooter
    footer = app._ctx.widgets.get("_sel_count_lbl")
    assert isinstance(footer, SelectionFooter)
    return footer


def test_the_footer_counts_the_selected_wells(loaded_app):
    app = loaded_app
    app._app_state.selection.selections = []      # per-well mode, not groups
    app._notebook.setCurrentByName("Plotting")
    app._plotting_notebook.setCurrentByName("Bar Plots")
    wells = sorted(app._app_state.dataset.well_paths)[:2]
    app._set_selected_wells(set(wells))
    app._refresh_sidebar_map_now()
    assert _footer(app).text() == "2 wells selected"
    assert not _footer(app).allButton().isHidden()


def test_all_is_not_offered_on_a_single_well_page(loaded_app):
    app = loaded_app
    app._app_state.selection.selections = []      # per-well mode, not groups
    app._notebook.setCurrentByName("smFISH")
    app._refresh_sidebar_map_now()
    assert _footer(app).allButton().isHidden(), (
        "All offered on a plate that holds one well")
    app._notebook.setCurrentByName("Plotting")
    app._refresh_sidebar_map_now()
    assert not _footer(app).allButton().isHidden()
