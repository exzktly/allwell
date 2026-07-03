"""Offscreen smoke tests for the Cell Browser tab.

Covers the lazy build, the sidebar wiring, and an ``apply_filters`` run against
the mini dataset — without asserting on pixel output (image composition runs on
a worker thread; here we only verify the synchronous control path is sound).
"""

from __future__ import annotations


def _goto_cell_browser(app):
    """Open the Cell Browser sub-tab of the unified Image Browser rail entry."""
    from well_viewer.features.image_browser.controller import show_image_browser_subtab
    assert show_image_browser_subtab(app, "Cell Browser")


def test_cell_browser_tab_builds_and_registers(loaded_app):
    app = loaded_app
    # Navigating to the tab forces its lazy builder to run.
    _goto_cell_browser(app)
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


def test_cell_browser_bar_survives_empty_refresh(loaded_app):
    """Regression: a built channel bar must NOT be wiped by a later refresh that
    transiently resolves no channels (channel state not ready / rows uncached).
    This is the "bar showed once then disappeared" bug."""
    app = loaded_app
    _goto_cell_browser(app)

    from well_viewer.features.image_browser.cell_browser.tab_view import cell_browser_refresh

    built = set(getattr(app, "_cell_browser_channel_chips", {}))
    assert built, "expected the bar to build from the dataset's channels"

    # Force the next resolve to come back empty (fluor list AND data both empty).
    app._app_state.channel.fluor_channels = []
    real_get_rows = app._get_rows
    app._get_rows = lambda _label: None
    try:
        cell_browser_refresh(app._ctx)
    finally:
        app._get_rows = real_get_rows

    # The bar must still be intact — not blanked.
    assert set(getattr(app, "_cell_browser_channel_chips", {})) == built


def test_cell_browser_section_icon_registered(viewer_app):
    # Cell Browser is now a sub-tab of the unified Image Browser rail entry —
    # it's the rail entry that carries the section icon, not the leaf.
    assert viewer_app._SECTION_ICONS.get("Image Browser")
    assert "Cell Browser" not in viewer_app._SECTION_ICONS


def test_cell_browser_click_navigates_to_segmentation(loaded_app):
    app = loaded_app
    _goto_cell_browser(app)
    app._set_selected_wells(set(app._app_state.dataset.well_paths))

    from well_viewer.features.image_browser.cell_browser import controller as ctl
    from well_viewer.features.image_browser.cell_browser.collect import collect_cells

    # None timepoints/FOVs → no tp/fov restriction (strict-list semantics: [] would
    # mean "0 selected → 0 cells"). We just need a cell to click.
    cells = collect_cells(app._ctx, [], None, None)
    assert cells, "expected the mini dataset to yield cells"

    # Clicking a cell must switch to Segmentation and select that nucleus —
    # the navigation path must not raise even when image arrays are absent.
    ctl.on_cell_clicked(app._ctx, cells[0])
    assert app._current_centre_tab() == "Segmentation"
    assert app._app_state.review_image.selected_nucleus == int(float(cells[0].nucleus_id))
    assert app._app_state.preview.selected_well == cells[0].well


def test_cell_browser_click_sets_normalized_timepoint(loaded_app):
    """Regression: clicking a cell must select the FOV/timepoint that actually
    matches the cell. The combos hold *normalized* tokens (timepoint as parsed
    hours, e.g. '24'), but the descriptor carries the raw CSV token ('24h'), so
    passing it straight to setCurrentText silently no-ops and the view stays on
    the wrong timepoint."""
    app = loaded_app
    _goto_cell_browser(app)

    from well_viewer.features.image_browser.cell_browser import controller as ctl
    from well_viewer.features.image_browser.cell_browser.collect import collect_cells

    cells = collect_cells(app._ctx, [], None, None)
    cell = next(c for c in cells if c.tp_token == "24h")
    assert app._norm_timepoint("24h") == "24"  # normalization actually differs

    class _Rec:
        def __init__(self):
            self.texts = []

        def setCurrentText(self, t):
            self.texts.append(str(t))

        def currentText(self):
            return self.texts[-1] if self.texts else ""

    fov_rec, tp_rec = _Rec(), _Rec()
    app._preview_fov_cb = fov_rec
    app._review_image_tp_cb = tp_rec

    ctl.on_cell_clicked(app._ctx, cell)

    # The raw token must never be the value handed to the combo; the normalized
    # hours value must be.
    assert "24h" not in tp_rec.texts
    assert "24" in tp_rec.texts
    assert fov_rec.texts and fov_rec.texts[-1] == "1"


def test_cell_browser_apply_filters_no_criteria(loaded_app):
    app = loaded_app
    _goto_cell_browser(app)

    from well_viewer.features.image_browser.cell_browser import controller as ctl

    # No criteria + all timepoints/FOVs selected → every cell in the selected
    # wells qualifies. apply_filters must populate the state without raising.
    ctl.apply_filters(app._ctx)
    st = app._cell_browser_state
    assert isinstance(st.cells, list)
    # loaded advances by at most one page; never exceeds the total.
    assert 0 <= st.loaded <= len(st.cells)


def test_compose_loads_channels_before_mask(monkeypatch):
    """Regression: _load_mask_array only resolves after a prior _load_array has
    populated the (well,'__mask__','raw') cache, so compose must load the
    channels first — otherwise every cell renders as the "—" placeholder."""
    import numpy as np
    from types import SimpleNamespace

    import well_viewer.features.image_browser.cell_browser.worker as w
    from well_viewer.features.image_browser.cell_browser.collect import CellDescriptor

    mask_key = ("A1", "__mask__", "raw")
    mask = np.zeros((10, 10), dtype=np.int32)
    mask[3:6, 3:6] = 7  # nucleus 7

    def fake_load_array(app, cache, well, ch, tp, fov, **kw):
        cache.setdefault(mask_key, True)        # the real side effect
        return np.ones((10, 10), dtype=np.float32)

    def fake_load_mask(app, cache, well, tp, fov):
        if mask_key not in cache:               # mirrors the real dependency
            return None
        return mask

    monkeypatch.setattr(w, "_load_array", fake_load_array)
    monkeypatch.setattr(w, "_load_mask_array", fake_load_mask)

    desc = CellDescriptor(well="A1", fov="1", tp_token="1", timepoint_h=0.0,
                          filename="f", nucleus_id="7", row_idx=0,
                          centroid_row=None, centroid_col=None)
    out = w.compose_cell_rgb(SimpleNamespace(), desc, ["gfp"], False, {})
    assert out is not None and out.shape[2] == 3
