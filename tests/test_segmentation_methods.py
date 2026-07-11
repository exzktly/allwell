"""Segmentation-method panel: the StarDist dropdown set, the per-method
parameter panel, and its collapse-to-current-page sizing. Runs headless
(offscreen Qt)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _set_method(tab, value: str) -> None:
    idx = tab._seg_method_cb.findData(value)
    assert idx >= 0, f"method {value!r} not in combo"
    tab._seg_method_cb.setCurrentIndex(idx)


# ── dropdown + per-method parameter panel ────────────────────────────────────

def test_dropdown_lists_stardist_methods(qapp):
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()
    values = [tab._seg_method_cb.itemData(i) for i in range(tab._seg_method_cb.count())]
    assert values == ["stardist_nuclei", "stardist_seeded_watershed_cell"]
    labels = [tab._seg_method_cb.itemText(i) for i in range(tab._seg_method_cb.count())]
    assert all("StarDist" in l for l in labels)


def test_param_panel_swaps_per_method(qapp):
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()
    for value in ("stardist_nuclei", "stardist_seeded_watershed_cell"):
        _set_method(tab, value)
        assert tab._seg_param_stack.currentName() == value

    # The seeded-watershed page exposes the cytoplasm-token + min-area controls.
    _set_method(tab, "stardist_seeded_watershed_cell")
    assert tab._cytoplasm_edit is not None
    assert tab._min_nucleus_area_edit is not None

    # The Segmentation-channel field stays visible for both StarDist methods.
    _set_method(tab, "stardist_nuclei")
    assert tab._nuclear_row_widget.isHidden() is False


def test_param_stack_collapses_to_current_page(qapp):
    # Regression: the param stack must size to the *current* page, not the max
    # over all pages — otherwise the empty nuclei page reserves the taller
    # watershed page's height (the gap under channel tokens).
    from PySide6.QtWidgets import QSizePolicy
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()
    st = tab._seg_param_stack
    _set_method(tab, "stardist_seeded_watershed_cell")
    tall = st.sizeHint().height()
    _set_method(tab, "stardist_nuclei")
    short = st.sizeHint().height()
    assert short < tall                      # collapsed, no reserved gap
    cur = st.currentIndex()
    assert st.widget(cur).sizePolicy().verticalPolicy() == QSizePolicy.Preferred
    assert all(st.widget(i).sizePolicy().verticalPolicy() == QSizePolicy.Ignored
               for i in range(st.count()) if i != cur)


# ── CLI arg mapping: only StarDist flags, no cellpose/classical flags ─────────

def _base(**over):
    opts = dict(nuclear_token="GFP", fluor_tokens=["GFP", "DAPI"], csv_prefix="m",
                filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_",
                min_nucleus_area_px=50, tf_threads="0", workers="0",
                compress_input_well_folders=True, compress_output_well_folders=True)
    opts.update(over)
    return opts


def test_build_pipeline_args_stardist_watershed():
    from pathlib import Path
    from services.pipeline_service import build_pipeline_args
    P = Path("/x/p.py")

    a = build_pipeline_args(P, Path("/in"), Path("/out"), _base(
        segmentation_method="stardist_seeded_watershed_cell",
        cytoplasm_token="DAPI"))
    assert a[a.index("--segmentation_method") + 1] == "stardist_seeded_watershed_cell"
    assert a[a.index("--cytoplasm_token") + 1] == "DAPI"
    # No cellpose flags survive the redesign.
    assert not any(x.startswith("--cellpose") for x in a)
