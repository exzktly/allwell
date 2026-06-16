"""Offscreen smoke tests for the Cell Browser tab.

Covers the lazy build, the sidebar wiring, and an ``apply_filters`` run against
the mini dataset — without asserting on pixel output (image composition runs on
a worker thread; here we only verify the synchronous control path is sound).
"""

from __future__ import annotations


def test_cell_browser_tab_builds_and_registers(loaded_app):
    app = loaded_app
    # Navigating to the tab forces its lazy builder to run.
    app._notebook.setCurrentByName("Cell Browser")
    assert app._current_centre_tab() == "Cell Browser"

    # Centre widgets wired.
    assert getattr(app, "_cell_browser_grid", None) is not None
    assert getattr(app, "_cell_browser_loader", None) is not None
    assert getattr(app, "_cell_browser_state", None) is not None

    # Sidebar frame is shown on this tab and its builder ran.
    assert app._sidebar_cell_browser_frame.isVisibleTo(app._sidebar_cell_browser_frame.parent())
    assert hasattr(app, "_cell_browser_criteria_rows")

    # Channel chips populated from the dataset's fluor channels.
    chips = getattr(app, "_cell_browser_channel_chips", {})
    assert set(chips) == set(app._app_state.channel.fluor_channels or [])


def test_cell_browser_section_icon_registered(viewer_app):
    assert viewer_app._SECTION_ICONS.get("Cell Browser") == "grid-3x3"
    # Heavy worker deps → lazy-only (builds on first click, not in the drain).
    assert "Cell Browser" in viewer_app._centre_lazy_only_titles


def test_cell_browser_click_navigates_to_segmentation(loaded_app):
    app = loaded_app
    app._notebook.setCurrentByName("Cell Browser")
    app._set_selected_wells(set(app._app_state.dataset.well_paths))

    from well_viewer.features.cell_browser import controller as ctl
    from well_viewer.features.cell_browser.collect import collect_cells

    cells = collect_cells(app, [], [], [])
    assert cells, "expected the mini dataset to yield cells"

    # Clicking a cell must switch to Segmentation and select that nucleus —
    # the navigation path must not raise even when image arrays are absent.
    ctl.on_cell_clicked(app, cells[0])
    assert app._current_centre_tab() == "Segmentation"
    assert app._app_state.review_image.selected_nucleus == int(float(cells[0].nucleus_id))
    assert app._app_state.preview.selected_well == cells[0].well


def test_cell_browser_apply_filters_no_criteria(loaded_app):
    app = loaded_app
    app._notebook.setCurrentByName("Cell Browser")

    from well_viewer.features.cell_browser import controller as ctl

    # No criteria + all timepoints/FOVs selected → every cell in the selected
    # wells qualifies. apply_filters must populate the state without raising.
    ctl.apply_filters(app)
    st = app._cell_browser_state
    assert isinstance(st.cells, list)
    # loaded advances by at most one page; never exceeds the total.
    assert 0 <= st.loaded <= len(st.cells)
