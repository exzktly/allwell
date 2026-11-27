"""Nothing open is ever more than one surface deep.

Two halves. The first holds the measure itself (``tests/_surface_checks.py``)
against widgets built here, so "depth" means what the rule means. The second
builds every Review page through its **real builder**, presses every enabled
button, presses every button inside whatever that opened, and asserts that no
press left more than one transient surface on screen.

This asserts what a press *opens*, which is behaviour: it is not a layout test
and does not belong in ``tests/_lanes.py::UI_LANE_FILES``.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import (
    QApplication, QDialog, QMenu, QMessageBox, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from tests import _surface_checks as surf

# ── the measure ───────────────────────────────────────────────────────────────


@pytest.fixture
def main(qtbot):
    w = QWidget()
    QVBoxLayout(w)
    qtbot.addWidget(w)
    w.resize(600, 400)
    w.show()
    return w


def test_a_page_with_nothing_open_has_depth_zero(main):
    assert surf.depth(main) == 0


def test_a_dialog_is_one_surface_and_a_menu_opened_from_it_is_two(main, qtbot):
    dlg = QDialog(main)
    dlg.show()
    assert surf.depth(main) == 1
    menu = QMenu(dlg)                 # a menu opened from inside the dialog
    menu.addAction("DMSO")
    menu.popup(dlg.mapToGlobal(dlg.rect().center()))
    assert surf.depth(main) == 2
    surf.close_transients(main)
    assert surf.depth(main) == 0


def test_an_alert_is_not_counted(main):
    box = QMessageBox(main)
    box.setText("Reset analysis?")
    box.show()
    try:
        assert surf.depth(main) == 0
    finally:
        box.close()


def test_a_popover_and_an_open_sheet_are_surfaces_and_the_desk_column_is_not(main):
    from PySide6.QtWidgets import QSplitter

    from widgets.docked_panel import DockedPanel
    from widgets.drawer import Drawer
    from widgets.popover import Popover
    split = QSplitter(main)
    main.layout().addWidget(split)
    split.addWidget(QWidget())
    column = DockedPanel(split, lambda: 200, side="right")
    column.open()
    assert surf.depth(main) == 0, "the docked inspector is a pane of the page, not a transient"
    sheet = Drawer(main, edge="bottom")
    sheet.setContentWidget(QWidget())
    sheet.open()
    assert surf.depth(main) == 1
    pop = Popover(main)
    pop.show()
    assert surf.depth(main) == 2
    surf.close_transients(main)
    assert surf.depth(main) == 0


def test_the_crawl_reports_a_menu_opened_from_a_dialog_and_passes_a_plain_menu(main, monkeypatch):
    surf.stub_blocking(monkeypatch)
    plain = QToolButton(main)
    plain.setText("Share")
    plain.setMenu(QMenu(plain))
    plain.menu().addAction("Copy figure")
    main.layout().addWidget(plain)
    QApplication.processEvents()      # a layout shows a new child on the next turn
    first = surf.crawl(main, main)
    assert first == [] and first.pressed == ["Share"]

    deep = QPushButton("Per-sample control…", main)
    main.layout().addWidget(deep)

    def _open():
        dlg = QDialog(main)
        dlg.setObjectName("PairingDialog")
        QVBoxLayout(dlg)
        inner = QToolButton(dlg)
        inner.setText("Set all to")
        inner.setMenu(QMenu(inner))
        inner.menu().addAction("DMSO")
        dlg.layout().addWidget(inner)
        dlg.exec()
    deep.clicked.connect(_open)
    QApplication.processEvents()
    found = surf.crawl(main, main)
    assert [f[0] for f in found] == ["Per-sample control… ▸ Set all to"]
    assert found[0][1] == 2



# ── every page, through its real builder ──────────────────────────────────────

#: The Review pages crawled here: every page the registry builds with
#: ``build(ctx, frame)``. A Plotting leaf is built with the Plotting page's
#: shared band registered first (``tests/_minimal_shell.build_page``), so its
#: controls are on screen as they are in the application.
PAGES = (
    "Home", "Statistics", "Multivariate", "Spatial", "Nanowells", "smFISH",
    "Review CSV", "Saved Cells", "Saved Plots",
    "Heat Map", "Bar Plots", "Line Graphs", "Response Curves", "Scatter Plot",
    "Distribution",
)

#: Pages the registry builds that this file cannot crawl, with what was
#: measured. They are crawled on the built application instead, in the slow
#: lane (``tests/test_every_page_renders.py``).
CRAWLED_ON_THE_BUILT_APP: dict[str, str] = {
    "Image Browser": "its leaves expect the rail's sidebar frames (`_sidebar_image_table_frame`, "
                     "`_sidebar_cell_browser_frame`), which the shell builds; pressed without "
                     "them the crawl segfaults in the minimal shell (measured)",
    "Batch Export": "its panels are built when the page is activated by the shell; built alone "
                    "its frame holds no button (measured: the crawl pressed nothing)",
}

#: Pages on which some press still leaves two surfaces open, with the row of
#: the surfaces plan that converts them. The roster only shrinks, and the last
#: row of the plan requires it empty.
PENDING: dict[str, str] = {}


def _built_page(qtbot, name: str, dataset):
    from tests._minimal_shell import build_page
    ctx, window, _frame, _bands = build_page(qtbot, name, dataset)
    return ctx, window, window._h_pane.widget(1)


def test_the_page_list_is_the_registry_s():
    """A page the registry gains is crawled by being declared: the list above
    is held against the registry, not maintained beside it."""
    from review.features.registry import build_feature_registry
    features = build_feature_registry()
    leaves_of_a_page = set(features.sub_leaves("Image Browser")) | {"Cell Gating", "Sample Map", "Ratios",
                                                                    "Pipeline Info", "QC Triage"}
    built = {d.name for d in features.all() if d.build is not None} - leaves_of_a_page
    assert built == set(PAGES) | set(CRAWLED_ON_THE_BUILT_APP)
    assert not set(PAGES) & set(CRAWLED_ON_THE_BUILT_APP)


@pytest.mark.parametrize("name", PAGES)
def test_no_press_on_a_page_opens_a_second_surface(name, qtbot, mini_dataset, monkeypatch):
    surf.stub_blocking(monkeypatch)
    _ctx, window, frame = _built_page(qtbot, name, mini_dataset)
    found = surf.crawl(frame, window)
    surf.close_transients(window)
    assert found.pressed, f"{name}: the crawl pressed nothing, so it proved nothing"
    if name in PENDING:
        assert found, (f"{name} is on the PENDING roster ({PENDING[name]}) but every press "
                       "now stays one surface deep — take it off the roster")
        return
    assert not found, f"{name}: a press left more than one surface open:\n  " + "\n  ".join(
        f"{label}: {n} open — {what}" for label, n, what in found)
