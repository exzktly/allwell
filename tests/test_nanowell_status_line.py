"""The Nanowells page's status line (``status_view``, ``SURFACES_TRACKER.md`` row S20): one chip under the figure per
condition that shapes it — Subset, excluded wells, a Distribution window, Cell Gating, a Resolve walk — each opening
the page that owns it, and no line at all when nothing applies.

Driven through the real page builder in the minimal shell of ``test_nanowell_page.py``: conditions are made the way
the page makes them (a drag on the figure, the band's Exclude, the controller's Subset), the chips are pressed, and
what they read and what the inspector shows is read back.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from review.core import inspector_service as svc  # noqa: E402
from review.features.nanowell import _anchor_queue as aq  # noqa: E402
from review.features.nanowell import _window_controller as wnc  # noqa: E402
from review.features.nanowell import anchor_queue_compute as aqc  # noqa: E402
from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell import subset_compute as ss  # noqa: E402
from tests.test_nanowell_page import _loaded, page  # noqa: E402,F401
from tests.test_nanowell_window_page import _drag, _first_panel  # noqa: E402


def _shown(ctx) -> dict[str, str]:
    """The chips on screen, by key, with what each reads."""
    from review.features.nanowell.status_view import CHIPS

    return {key: ctx.widgets.get(f"_nw_status_{key}").text() for key in CHIPS
            if ctx.widgets.get(f"_nw_status_{key}").isVisibleTo(ctx.window)}


def test_nothing_applies_and_there_is_no_line(page, qtbot):  # noqa: F811
    _loaded(qtbot, page)
    assert _shown(page) == {}
    assert not page.widgets.get("_nw_status_line").isVisibleTo(page.window)


def test_an_excluded_well_has_a_chip_whose_menu_brings_it_back(page, qtbot):  # noqa: F811
    _loaded(qtbot, page)
    nwc.set_well_excluded(page, "A01", True)
    assert _shown(page) == {"excluded": "Excluded: A01"}
    chip = page.widgets.get("_nw_status_excluded")
    assert chip.menu() is page.widgets.get("_nw_excluded_wells_btn").menu()
    restore = next(a for a in chip.menu().actions() if a.text() == "Restore A01")
    restore.trigger()
    assert page.state.feature_slice("nanowell").excluded_wells == []
    assert _shown(page) == {}


def test_the_subset_chip_opens_the_subset_s_conditions(page, qtbot):  # noqa: F811
    _loaded(qtbot, page)
    nwc.set_subset(page, ss.Subset((ss.Condition("n0", "ge", 1),)))
    st = page.state.feature_slice("nanowell")
    assert _shown(page) == {"subset": f"Subset: {st.subset.label()} ›"}
    qtbot.mouseClick(page.widgets.get("_nw_status_subset"), Qt.LeftButton)
    from review.core import inspector_panels as panels

    conditions = panels.panel_of(page.widgets.get("_nw_subset_edit_btn")).content
    assert svc.is_showing(page, conditions)


def test_a_window_drawn_opens_its_page_and_clearing_it_leaves_it(page, qtbot):  # noqa: F811
    _loaded(qtbot, page)
    _drag(page, _first_panel(page), (-0.4, 1.5), (1.4, 4.5))
    st = page.state.feature_slice("nanowell")
    window = wnc.current(st)[0]
    assert _shown(page) == {"window": f"Window: {window.lo:.3g}–{window.hi:.3g} ›"}
    strip = page.widgets.get("_nw_window_label").parentWidget()
    # Drawn just now: its page opens unasked, as the strip appeared, holding the sentence and the fields.
    assert svc.is_showing(page, strip) and page.widgets.get("_nw_window_lo").isVisibleTo(page.window)
    svc.pop(page)
    assert not svc.is_showing(page, strip)
    qtbot.mouseClick(page.widgets.get("_nw_status_window"), Qt.LeftButton)       # the chip opens it again
    assert svc.is_showing(page, strip)
    qtbot.mouseClick(page.widgets.get("_nw_window_clear_btn"), Qt.LeftButton)
    assert _shown(page) == {} and not svc.is_showing(page, strip)


def test_cell_gating_has_a_chip_that_opens_cell_gating(page, qtbot, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    _loaded(qtbot, page)
    st = page.state.feature_slice("nanowell")
    st.gate = SimpleNamespace(excluded=1204, fields=(), cells=5000, unread=())
    page.widgets.get("_nw_status_refresh")()
    assert _shown(page) == {"gates": "Cell Gating: 1,204 cells left out ›"}
    asked = []
    monkeypatch.setattr(page.features, "query", lambda name, *a, **k: asked.append(name))
    qtbot.mouseClick(page.widgets.get("_nw_status_gates"), Qt.LeftButton)
    assert asked == ["cell_gating.show"]
    nwc.set_view(page, "qc")                                     # QC draws no nanowell's value: gating changes nothing
    assert "gates" not in _shown(page)


def test_a_walk_has_a_chip_and_opens_its_guide_and_ending_it_leaves_the_page(page, qtbot):  # noqa: F811
    _loaded(qtbot, page)
    st = page.state.feature_slice("nanowell")
    queue = aqc.AnchorQueue([aqc.QueueItem("A01", "t_a"), aqc.QueueItem("A01", "t_c")], index=0,
                            data_dir=str(page.state.dataset.data_dir))
    st.anchor_queue = queue
    nwc.set_view(page, aq.current_view(page))
    page.widgets.get("_nw_refresh_controls")()
    assert _shown(page) == {"walk": f"Resolve: {queue.position_text()} ›"}
    guide = page.widgets.get("_nw_resolve_guide")
    assert svc.is_showing(page, guide) and page.widgets.get("_nw_resolve_problem").isVisibleTo(page.window)
    # Back, Skip and Stop stay on the band, in the Well's place, with their keys, and are on the page as well.
    assert page.widgets.get("_nw_queue_skip_btn").isVisibleTo(page.window)
    assert not page.widgets.get("_nw_resolve_back_btn").isEnabled()           # nothing before the first timepoint
    qtbot.mouseClick(page.widgets.get("_nw_resolve_skip_btn"), Qt.LeftButton)
    assert queue.index == 1 and page.widgets.get("_nw_resolve_back_btn").isEnabled()
    assert page.widgets.get("_nw_status_walk").text() == f"Resolve: {queue.position_text()} ›"
    # Going to another view ends the walk (``_anchor_queue``): its chip goes and its page is left.
    nwc.set_view(page, "trajectories" if aq.current_view(page) != "trajectories" else "distribution")
    assert aq.active_queue(page) is None
    assert _shown(page) == {} and not svc.is_showing(page, guide)
