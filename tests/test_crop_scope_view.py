"""The Segmentation tab's Crop-scope popover.

Same widget as the Delete-scope popover (``frame_scope_view``) with the opposite
un-armed default, so what is tested here is the difference: an untouched field
means *every* frame, the popover says so, and one edit narrows one field without
pinning the other two to whatever happened to be on screen.

Built against a stub ctx — Qt widgets, no ``WellViewerApp`` — so the seeding, the
arming and the button readout are exercised rather than reasoned about. The
armed scope decides which cells count toward every downstream number, so what
the button says has to match what the state holds.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
pd = pytest.importorskip("pandas")

from PySide6.QtWidgets import QPushButton, QWidget

from widgets.checkable_dropdown import CheckableDropdown
from well_viewer.features.crop.controller import CROP_SCOPE
from well_viewer.features.image_browser.review_image.state import ReviewImageState
from well_viewer.features.preview.crop_scope_view import (
    build_crop_scope_control, describe_crop_scope,
)


class _Widgets:
    """Minimal stand-in for ``ctx.widgets``."""

    def __init__(self):
        self._by_name: dict = {}

    def register(self, name, widget):
        self._by_name[name] = widget
        return widget

    def get_or_none(self, name):
        return self._by_name.get(name)


class _Combo:
    def __init__(self, text):
        self._text = text

    def currentText(self):
        return self._text


def _df():
    return pd.DataFrame({
        "shape_centroid_row": [2, 50],
        "shape_centroid_col": [3, 60],
    })


def _ctx(*, well="A01", fov="1", tp="0", wells=("A01", "B02", "C03"),
         fovs=("1", "2"), tps=(0.0, 24.0), selected=(), region=(0, 0, 10, 10)):
    statuses: list[str] = []
    ctx = SimpleNamespace(
        state=SimpleNamespace(
            review_image=ReviewImageState(),
            preview=SimpleNamespace(selected_well=well),
            dataset=SimpleNamespace(
                well_paths={w: f"/tmp/{w}.csv" for w in wells},
                cache={w: _df() for w in wells},
                all_fovs_cache=list(fovs),
                all_timepoints_cache=list(tps),
                crop_region=region,
                crop_scope_wells=None,
                crop_scope_fovs=None,
                crop_scope_tps=None,
            ),
            selection=SimpleNamespace(selected_wells=set(selected)),
        ),
        runtime=SimpleNamespace(
            review_image=SimpleNamespace(fov_menu=_Combo(fov), tp_cb=_Combo(tp)),
        ),
        widgets=_Widgets(),
        notify=SimpleNamespace(set_status=statuses.append),
        persistence=None,
    )
    ctx.statuses = statuses
    return ctx


@pytest.fixture
def built(qtbot):
    ctx = _ctx()
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = build_crop_scope_control(ctx, parent)
    # ``parent`` has to outlive the test: dropping the last Python reference
    # deletes the C++ widget and every child button with it.
    return ctx, btn, parent


def _dropdowns(btn):
    return btn.menu().findChildren(CheckableDropdown)


def test_starts_unarmed_and_says_all_frames(built):
    ctx, btn, _parent = built
    ds = ctx.state.dataset
    assert (ds.crop_scope_wells, ds.crop_scope_fovs, ds.crop_scope_tps) == (
        None, None, None,
    )
    assert describe_crop_scope(ctx) == "Crop scope: all frames"
    assert btn.text().startswith("Crop scope: all frames")


def test_unarmed_popover_seeds_every_option_not_the_displayed_frame(built):
    """The delete's popover seeds the frame on screen because that is what its
    un-armed field means. The crop's means every value, and showing one ticked
    well would misstate a crop that is filtering the whole plate."""
    ctx, btn, _parent = built
    btn.menu().aboutToShow.emit()
    wells_dd, fovs_dd, tps_dd = _dropdowns(btn)
    assert sorted(str(d) for d in wells_dd.checkedData()) == ["A01", "B02", "C03"]
    assert sorted(str(d) for d in fovs_dd.checkedData()) == ["1", "2"]
    assert sorted(str(d) for d in tps_dd.checkedData()) == ["0", "24"]
    # Looking at the scope is not setting it.
    ds = ctx.state.dataset
    assert (ds.crop_scope_wells, ds.crop_scope_fovs, ds.crop_scope_tps) == (
        None, None, None,
    )


def test_ticking_one_dropdown_arms_only_that_field(built):
    ctx, btn, _parent = built
    btn.menu().aboutToShow.emit()
    _dropdowns(btn)[0].setCheckedData(["A01", "B02"])
    ds = ctx.state.dataset
    assert ds.crop_scope_wells == {"A01", "B02"}
    assert ds.crop_scope_fovs is None
    assert ds.crop_scope_tps is None
    assert btn.text().startswith("Crop scope: 2 wells · all FOVs · all TPs")


def test_arming_reprojects_crop_pass_once_the_burst_settles(built):
    """Changing the scope changes which cells are excluded, exactly as moving
    the box would, so it takes the same recompute path rather than waiting for
    the next cache miss. It waits only for the run of ticks to finish —
    closing the popover is the user saying they are done."""
    ctx, btn, _parent = built
    btn.menu().aboutToShow.emit()
    _dropdowns(btn)[0].setCheckedData(["A01"])
    btn.menu().aboutToHide.emit()
    cache = ctx.state.dataset.cache
    assert list(cache["A01"]["Crop_Pass"]) == [1, 0]   # in scope: box applies
    assert list(cache["B02"]["Crop_Pass"]) == [1, 1]   # out of scope: untouched
    assert any("Crop scope updated" in s for s in ctx.statuses)


def test_reset_widens_back_to_every_frame(built):
    ctx, btn, _parent = built
    ctx.state.dataset.crop_scope_wells = {"A01"}
    reset = next(b for b in btn.menu().findChildren(QPushButton)
                 if b.text() == CROP_SCOPE.reset_label)
    reset.click()
    ds = ctx.state.dataset
    assert (ds.crop_scope_wells, ds.crop_scope_fovs, ds.crop_scope_tps) == (
        None, None, None,
    )
    assert btn.text().startswith("Crop scope: all frames")
    assert list(ds.cache["A01"]["Crop_Pass"]) == [1, 0]


def test_use_plate_selection_arms_selected_wells_without_writing_them(qtbot):
    ctx = _ctx(selected=("B02", "C03", "Z99"))
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = build_crop_scope_control(ctx, parent)
    plate = next(b for b in btn.menu().findChildren(QPushButton)
                 if b.text() == "Use plate selection")
    plate.click()
    # Z99 is not a loaded well, so it is filtered out.
    assert ctx.state.dataset.crop_scope_wells == {"B02", "C03"}
    # the app-wide plate selection is untouched
    assert ctx.state.selection.selected_wells == {"B02", "C03", "Z99"}


def test_scope_change_without_a_box_says_so_rather_than_excluded_zero(qtbot):
    """Arming frames before drawing is a legitimate order of operations;
    "excluded 0 cells" would read as a crop that failed."""
    ctx = _ctx(region=None)
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = build_crop_scope_control(ctx, parent)
    btn.menu().aboutToShow.emit()
    _dropdowns(btn)[0].setCheckedData(["A01"])
    btn.menu().aboutToHide.emit()
    assert any("draw a crop rectangle" in s for s in ctx.statuses)


# ── a burst of ticks is one recompute, not one per tick ──────────────────────
def test_each_tick_writes_state_but_only_one_recompute_runs(built, monkeypatch):
    """``CheckableDropdown`` emits ``changed`` once per item, and the crop's
    re-derivation re-projects every cached well, redraws the plot and re-renders
    the canvas. Ticking eleven wells one at a time ran eleven of those on the UI
    thread. The state and the button still move on every tick."""
    from well_viewer.features.preview import frame_scope_view

    ctx, btn, _parent = built
    calls: list[int] = []
    monkeypatch.setattr(frame_scope_view, "apply_scope_change",
                        lambda *_a, **_k: calls.append(1))
    btn.menu().aboutToShow.emit()
    wells_dd = _dropdowns(btn)[0]
    for picked in (["A01"], ["A01", "B02"], ["A01", "B02", "C03"]):
        wells_dd.setCheckedData(picked)
    assert ctx.state.dataset.crop_scope_wells == {"A01", "B02", "C03"}
    assert btn.text().startswith("Crop scope: all wells")   # 3 of 3
    assert calls == []                                      # still settling
    btn.menu().aboutToHide.emit()                           # closing flushes it
    assert calls == [1]


def test_closing_the_popover_with_nothing_pending_recomputes_nothing(built,
                                                                     monkeypatch):
    from well_viewer.features.preview import frame_scope_view

    _ctx_, btn, _parent = built
    calls: list[int] = []
    monkeypatch.setattr(frame_scope_view, "apply_scope_change",
                        lambda *_a, **_k: calls.append(1))
    btn.menu().aboutToShow.emit()
    btn.menu().aboutToHide.emit()
    assert calls == []


def test_plate_selection_leaves_the_effect_number_on_the_status_line(qtbot):
    """``on_change`` reports how many cells the crop now excludes. Running it
    before the "armed N wells" line left the user with the number-free half."""
    ctx = _ctx(selected=("B02", "C03"))
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = build_crop_scope_control(ctx, parent)
    plate = next(b for b in btn.menu().findChildren(QPushButton)
                 if b.text() == "Use plate selection")
    plate.click()
    assert "Crop scope updated" in ctx.statuses[-1]


def test_scope_change_is_persisted(qtbot):
    """The scope is part of what a saved crop means, so a change schedules the
    same save the region does."""
    saved: list[str] = []
    ctx = _ctx()
    ctx.persistence = SimpleNamespace(schedule_save=saved.append)
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = build_crop_scope_control(ctx, parent)
    btn.menu().aboutToShow.emit()
    _dropdowns(btn)[0].setCheckedData(["A01"])
    btn.menu().aboutToHide.emit()
    assert "crop_region" in saved


def test_a_pending_change_is_not_lost_when_the_popover_is_destroyed(qtbot):
    """The state is written and the button relabelled on the tick; dropping the
    deferred half would leave the row cache projected against a scope the user
    can see armed, silently, for the rest of the session."""
    ctx = _ctx()
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = build_crop_scope_control(ctx, parent)
    btn.menu().aboutToShow.emit()
    _dropdowns(btn)[0].setCheckedData(["A01"])
    assert "Crop_Pass" not in ctx.state.dataset.cache["A01"].columns

    from PySide6.QtCore import QCoreApplication, QEvent
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    assert list(ctx.state.dataset.cache["A01"]["Crop_Pass"]) == [1, 0]
    assert list(ctx.state.dataset.cache["B02"]["Crop_Pass"]) == [1, 1]
