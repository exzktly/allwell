"""The Saved Cells page lists the subsets saved on the Nanowells page, with nothing to do to put them there.

Asked for: "On nanowells you can define and save subsets. I want that to appear on the saved cells page. No
additional clicks." These drive the real Saved Cells page and the Nanowells page's own save, rename, delete and
apply calls (``controller.save_subset`` and friends, which the Subset box's buttons call), so a subset reaches the
list the way a user's does — and the list is read from the page's rows as drawn (CLAUDE.md §7.4 #1).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

pytestmark = pytest.mark.qt


@pytest.fixture
def nw(qtbot, tmp_path):
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root / "out")
    ctx.state.dataset.data_dir = root / "out"
    return ctx


def _page(ctx):
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from review.features.saved_cells.tab_view import build_saved_cells_tab

    host = QWidget(ctx.window)
    QVBoxLayout(host)
    build_saved_cells_tab(ctx, host)
    ctx.window.setCentralWidget(host)
    ctx.window.show()
    return ctx.widgets.get("_saved_cells_page")


def _rows(table):
    return [tuple(table.item(r, c).text() for c in range(table.columnCount())) for r in range(table.rowCount())]


def _subset(n):
    from review.features.nanowell import subset_compute as ss

    return ss.Subset((ss.Condition("n0", "ge", n),)).validated()


def _save(ctx, name, subset):
    from review.features.nanowell import controller as nwc

    ctx.state.feature_slice("nanowell").subset = subset
    assert nwc.save_subset(ctx, name) == ""


def test_a_subset_saved_on_the_nanowells_page_appears_on_the_open_page_at_once(nw):
    page = _page(nw)
    assert page.subsets_empty.isVisibleTo(page) and not page.subsets.isVisibleTo(page)
    _save(nw, "Singles+", _subset(2))                  # no refresh, no click: the save alone
    assert page.subsets.isVisibleTo(page) and not page.subsets_empty.isVisibleTo(page)
    assert _rows(page.subsets) == [(
        "Singles+", _subset(2).label(), "Not counted until Nanowells has read its wells",
        "The Nanowells page's Subset — drawn there and on the nanowell Scatter")]


def test_rename_delete_and_a_changed_subset_reach_the_list(nw):
    from review.features.nanowell import controller as nwc

    page = _page(nw)
    _save(nw, "Two", _subset(2))
    _save(nw, "Three", _subset(3))
    assert [r[0] for r in _rows(page.subsets)] == ["Two", "Three"]
    assert [r[3] for r in _rows(page.subsets)] == ["Saved only — changes no number",
                                                   "The Nanowells page's Subset — drawn there and on the nanowell Scatter"]
    assert nwc.rename_saved_subset(nw, "Two", "Doubles") == ""
    assert [r[0] for r in _rows(page.subsets)] == ["Doubles", "Three"]
    assert nwc.set_subset(nw, _subset(2))              # the page's Subset is now the one saved as Doubles
    assert [r[3] for r in _rows(page.subsets)][0].startswith("The Nanowells page's Subset")
    nwc.delete_saved_subset(nw, "Three")
    assert [r[0] for r in _rows(page.subsets)] == ["Doubles"]


def test_the_count_is_the_page_s_once_its_wells_are_read(nw):
    from review.features.nanowell.collect import load_nanowell_data
    from review.features.nanowell.series_compute import site_metrics

    st = nw.state.feature_slice("nanowell")
    st.data = load_nanowell_data(nw.state.dataset.data_dir, wells=["A01", "B01"], parts={})
    st.metrics = site_metrics(st.data, "earliest")
    page = _page(nw)
    _save(nw, "Two", _subset(2))
    # Starting cells ≥ 2 at 0h: A01's (1,0) and (-1,1), B01's (0,0) — three of the ten nanowells.
    assert _rows(page.subsets)[0][2] == "3 of 10 nanowells"


def test_use_on_nanowells_page_makes_it_the_page_s_subset(nw, monkeypatch):
    shown = []
    notebook = type("Nb", (), {"setCurrentByName": lambda self, name: shown.append(name) or True})()
    monkeypatch.setattr(nw.window, "_notebook", notebook, raising=False)
    page = _page(nw)
    _save(nw, "Two", _subset(2))
    _save(nw, "Three", _subset(3))
    page.subsets.selectRow(0)
    page.use_subset.click()
    assert nw.state.feature_slice("nanowell").subset == _subset(2)
    assert shown == ["Nanowells"]


def test_a_standard_plate_shows_no_subset_section(qtbot, mini_dataset_shape):
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, mini_dataset_shape)
    ctx.state.dataset.data_dir = mini_dataset_shape
    page = _page(ctx)
    assert not any(w.isVisibleTo(page) for w in (page.subsets_header, page.subsets, page.subsets_empty,
                                                  page.use_subset))
