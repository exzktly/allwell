"""The Heat Map's row and column names are a page you can see.

Double-clicking a tick label renames it; so does **Display › Labels**, which is
the route a user can find by looking and the only one in phone layout. Both end
in ``labels_view.rename_label``. These tests go through the real Heat Map page
(``tests/_minimal_shell.build_page``): they press the row, type into a field of
the page, and assert what reached the layout model.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit

from review.core import inspector_service as svc
from review.features.heatmap import labels_view as LBL
from review.features.heatmap.layout_view import _ensure_sidebar_layout
from tests import _panels
from tests._minimal_shell import build_page


def _page(qtbot, mini_dataset):
    ctx, _window, _frame, _bands = build_page(qtbot, "Heat Map", mini_dataset)
    layout = _ensure_sidebar_layout(ctx)
    layout.resize(2, 3)
    return ctx, layout


def _field(ctx, kind: str, idx: int) -> QLineEdit:
    host = ctx.widgets.get("_heatmap_labels_form")
    found = host.findChild(QLineEdit, f"HeatmapLabel_{kind}_{idx}")
    assert found is not None, f"the Labels page has no field for {kind} {idx}"
    return found


def test_the_display_tab_has_a_labels_row_that_opens_a_field_per_row_and_column(qtbot, mini_dataset):
    ctx, _layout = _page(qtbot, mini_dataset)
    button = ctx.widgets.get("_heatmap_labels_button")
    assert button.text() == "Labels ›"
    button.click()
    assert svc.is_showing(ctx, _panels.content(button))
    assert svc.inspector(ctx).currentTab() == "display"
    host = ctx.widgets.get("_heatmap_labels_form")
    names = sorted(e.objectName() for e in host.findChildren(QLineEdit))
    assert names == ["HeatmapLabel_col_0", "HeatmapLabel_col_1", "HeatmapLabel_col_2",
                     "HeatmapLabel_row_0", "HeatmapLabel_row_1"]


def test_typing_a_name_renames_that_tick_and_marks_it_the_user_s(qtbot, mini_dataset):
    ctx, layout = _page(qtbot, mini_dataset)
    button = ctx.widgets.get("_heatmap_labels_button")
    button.click()
    edit = _field(ctx, "col", 1)
    edit.setText("  10 µM ")
    edit.editingFinished.emit()
    assert layout.col_labels == ["1", "10 µM", "3"]
    assert layout.col_labels_typed == [1]
    assert layout.row_labels in (None, [], ["1", "2"]) and not layout.row_labels_typed
    assert button.text() == "Labels: 1 named ›"

    edit = _field(ctx, "row", 1)
    edit.setText("HeLa")
    edit.editingFinished.emit()
    assert layout.row_labels == ["1", "HeLa"] and layout.row_labels_typed == [1]


def test_emptying_a_field_puts_the_grid_s_own_label_back(qtbot, mini_dataset):
    ctx, layout = _page(qtbot, mini_dataset)
    LBL.rename_label(ctx, "row", 0, "Control")
    button = ctx.widgets.get("_heatmap_labels_button")
    button.click()
    edit = _field(ctx, "row", 0)
    assert edit.text() == "Control"
    edit.setText("")
    edit.editingFinished.emit()
    assert layout.row_labels == ["1", "2"]
    assert layout.row_labels_typed == []


def test_leaving_a_field_untouched_writes_nothing(qtbot, mini_dataset):
    ctx, layout = _page(qtbot, mini_dataset)
    button = ctx.widgets.get("_heatmap_labels_button")
    button.click()
    _field(ctx, "col", 0).editingFinished.emit()
    assert not layout.col_labels_typed and not layout.col_labels


def test_the_double_click_and_the_page_rename_through_the_same_function(qtbot, mini_dataset, monkeypatch):
    """The gesture is a shortcut to the page: what the dialog returns goes to
    ``rename_label``, with the tick the click landed on."""
    import types

    from PySide6.QtWidgets import QInputDialog

    from review.features.heatmap import tab_view as TV

    ctx, layout = _page(qtbot, mini_dataset)
    box = types.SimpleNamespace(contains=lambda x, y: True)
    tick = types.SimpleNamespace(get_window_extent=lambda renderer=None: box)
    ax = types.SimpleNamespace(get_xticklabels=lambda: [], get_yticklabels=lambda: [tick, tick])
    canvas = types.SimpleNamespace(get_renderer=lambda: None)
    real = ctx.widgets.get_or_none
    monkeypatch.setattr(type(ctx.widgets), "get_or_none",
                        lambda self, key: {"_heatmap_ax": ax, "_heatmap_canvas": canvas}.get(key)
                        or real(key))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Row A", True)))
    TV._on_label_double_click(ctx, types.SimpleNamespace(dblclick=True, x=1, y=1))
    assert layout.row_labels == ["Row A", "2"] and layout.row_labels_typed == [0]
