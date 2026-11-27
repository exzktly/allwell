"""The Image Table's style options are the inspector's Style tab.

Spacing, label fonts, the scale bar and cell labels are opened by the page's
**Style** button as the first page of the inspector's Style tab — beside the
page, like every plot's style panel — not in a dock of the page's own and not
in the left sidebar.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.mark.qt
def test_style_opens_in_the_inspector_not_in_a_dock_or_the_sidebar(image_table_app):
    from review.core import inspector_service as svc
    from widgets.stepper import Stepper

    ctx = image_table_app._ctx
    assert image_table_app._notebook.setCurrentByName("Image Browser")
    button = ctx.widgets.get("_image_table_style_btn")
    panel = ctx.widgets.get("_image_table_style_panel")
    assert button.text() == "Style"
    assert not svc.is_open(ctx)                       # closed by default
    # 3 spacing gaps + 2 axis label font sizes + scale-bar (pixel size, bar length,
    # bar thickness) + cell-label font size = 9 steppers.
    assert len(panel.findChildren(Stepper)) == 9

    button.click()
    insp = svc.inspector(ctx)
    assert svc.is_open(ctx) and insp.currentTab() == "style"
    assert insp.tabWidget("style") is panel and insp.isAncestorOf(panel)
    button.click()
    assert not svc.is_open(ctx)                       # and closes it again
    assert not insp.isAncestorOf(panel)

    # The controls live in the inspector's page, not the left sidebar.
    sframe = ctx.widgets.get("_sidebar_image_table_frame")
    assert not sframe.findChildren(Stepper)


@pytest.mark.qt
def test_set_spacing_rerenders_once_a_table_is_built(image_table_app, monkeypatch):
    """A Properties tweak must take effect as soon as a table has been built —
    not only after a first Generate (the old ``last_render`` gate silently
    dropped the first adjustment)."""
    from review.features.image_browser.image_table import controller as C

    ctx = image_table_app._ctx
    calls = []
    monkeypatch.setattr(C, "image_table_generate", lambda a: calls.append(a))

    # No cells yet → nothing to re-render.
    ctx.runtime.image_table.cells = []
    ctx.state.image_table.last_render = {}
    C.image_table_set_spacing(ctx, "cell_gap", 12)
    assert ctx.state.image_table.cell_gap == 12
    assert calls == []

    # Table built but never Generated (last_render still empty) → must re-render.
    C.image_table_apply_preset(ctx, "channel", "timepoint")
    assert ctx.runtime.image_table.cells
    ctx.state.image_table.last_render = {}
    C.image_table_set_spacing(ctx, "row_group_gap", 30)
    assert ctx.state.image_table.row_group_gap == 30
    assert calls, "set_spacing must re-render once cells exist, even pre-Generate"


@pytest.mark.qt
def test_share_names_its_entries_as_every_plot_page_does_in_the_same_order(image_table_app):
    """The plot pages' Share reads Save figure…, Copy figure as SVG, then Save to
    Saved Plots (``ui_helpers.build_plot_save_menu``). The Image Table's said
    "Export to File…" and "Copy SVG" for the same jobs."""
    from PySide6.QtWidgets import QToolButton

    window = image_table_app
    share = next(b for b in window.findChildren(QToolButton)
                 if b.property("bandRole") == "share" and b.menu() is not None
                 and "Save figure…" in [a.text() for a in b.menu().actions()])
    names = [a.text() for a in share.menu().actions() if a.text()]
    assert "Export to File…" not in names and "Copy SVG" not in names
    order = [n for n in names if n in ("Save figure…", "Copy figure as SVG", "Save to Saved Plots")]
    assert order == ["Save figure…", "Copy figure as SVG", "Save to Saved Plots"], names


@pytest.mark.qt
def test_export_settings_are_rows_of_the_style_page_and_reach_the_exporter(image_table_app):
    """The "Export settings…" dialog and the Saved Plots quality submenu are
    rows of the Style page now, applied as they change — and what they write is
    what the exporter reads."""
    from PySide6.QtWidgets import QCheckBox, QComboBox, QSpinBox

    from review.features.image_browser.image_table import export as E

    ctx = image_table_app._ctx
    panel = ctx.widgets.get("_image_table_style_panel")
    quality = panel.findChild(QComboBox, "ImageTableGalleryQuality")
    dpi = panel.findChild(QSpinBox, "ImageTableDpi")
    transparent = panel.findChild(QCheckBox, "ImageTableTransparent")
    assert quality is not None and dpi is not None and transparent is not None
    keys = list(E.GALLERY_QUALITY_PRESETS)
    other = next(k for k in keys if k != quality.currentData())
    quality.setCurrentIndex(keys.index(other))
    assert E.gallery_quality_px(ctx) == E.GALLERY_QUALITY_PRESETS[other]
    dpi.setValue(450)
    transparent.setChecked(not transparent.isChecked())
    opts = E._export_opts(ctx)
    assert opts["dpi"] == 450 and opts["transparent_bg"] is transparent.isChecked()
    names = [a.text() for a in _share_menu(image_table_app).actions()]
    assert "Export settings…" not in names and "Save to Saved Plots quality" not in names


def _share_menu(window):
    from PySide6.QtWidgets import QToolButton
    return next(b.menu() for b in window.findChildren(QToolButton)
                if b.property("bandRole") == "share" and b.menu() is not None
                and "Save figure…" in [a.text() for a in b.menu().actions()])


@pytest.mark.qt
def test_crop_view_s_first_entry_is_a_command_that_says_what_it_will_do(image_table_app):
    from review.features.image_browser.image_table import tab_view as TV

    ctx = image_table_app._ctx
    act = ctx.widgets.get("_image_table_crop_act")
    menu = act.parent()
    tool = ctx.runtime.image_table.crop_tool
    assert not act.isCheckable()
    menu.aboutToShow.emit()
    assert act.text() == TV.CROP_START_TEXT and not tool.mode
    act.trigger()
    menu.aboutToShow.emit()
    assert tool.mode and act.text() == TV.CROP_STOP_TEXT
    act.trigger()
    assert not tool.mode
