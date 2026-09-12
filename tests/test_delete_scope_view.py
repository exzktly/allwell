"""The Segmentation tab's Delete-scope popover.

Builds the real control against a stub ctx — Qt widgets, no ``ReviewApp`` —
so the seeding, the arming and the button readout are exercised rather than
reasoned about. The armed scope is the one piece of this feature a user can get
wrong expensively, so what the button says has to match what the state holds.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from review.features.image_browser.review_image.state import ReviewImageState
from review.features.preview.delete_scope_view import (
    build_delete_scope_control, describe_delete_scope,
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


def _ctx(*, well="A01", fov="1", tp="0", wells=("A01", "B02", "C03"),
         fovs=("1", "2"), tps=(0.0, 24.0), selected=()):
    return SimpleNamespace(
        state=SimpleNamespace(
            review_image=ReviewImageState(),
            preview=SimpleNamespace(selected_well=well),
            dataset=SimpleNamespace(
                well_paths={w: f"/tmp/{w}.csv" for w in wells},
                all_fovs_cache=list(fovs),
                all_timepoints_cache=list(tps),
            ),
            selection=SimpleNamespace(selected_wells=set(selected)),
        ),
        runtime=SimpleNamespace(
            review_image=SimpleNamespace(fov_menu=_Combo(fov), tp_cb=_Combo(tp)),
        ),
        widgets=_Widgets(),
        notify=SimpleNamespace(set_status=lambda msg: None),
    )


@pytest.fixture
def built(qtbot):
    ctx = _ctx()
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = build_delete_scope_control(ctx, parent)
    # ``parent`` has to outlive the test: dropping the last Python reference
    # deletes the C++ widget and every child button with it.
    return ctx, btn, parent


def test_starts_unarmed_and_says_so(built):
    ctx, btn, _parent = built
    ri = ctx.state.review_image
    assert (ri.delete_scope_wells, ri.delete_scope_fovs, ri.delete_scope_tps) == (
        None, None, None,
    )
    assert describe_delete_scope(ctx) == "Delete scope: this frame"
    assert btn.text().startswith("Delete scope: this frame")


def test_opening_the_popover_arms_nothing(built):
    """Seeding the dropdowns from the displayed frame must not count as an
    edit — looking at the scope is not setting it."""
    ctx, btn, _parent = built
    btn.menu().aboutToShow.emit()
    ri = ctx.state.review_image
    assert ri.delete_scope_wells is None
    assert ri.delete_scope_fovs is None
    assert ri.delete_scope_tps is None
    assert btn.text().startswith("Delete scope: this frame")


def test_readout_names_counts_and_all(built):
    ctx, _btn, _parent = built
    ri = ctx.state.review_image
    ri.delete_scope_wells = {"A01", "B02"}
    ri.delete_scope_fovs = {"1", "2"}          # every FOV in the dataset
    ri.delete_scope_tps = {"0"}
    assert describe_delete_scope(ctx) == (
        "Delete scope: 2 wells · all FOVs · 1 TP"
    )


def test_emptied_field_reads_as_none_not_all(built):
    """An emptied dropdown selects nothing. Reading it as "all" would turn a
    stray click into a plate-wide delete."""
    ctx, _btn, _parent = built
    ctx.state.review_image.delete_scope_wells = set()
    assert "no wells" in describe_delete_scope(ctx)


def test_reset_disarms_and_relabels(built, qtbot):
    ctx, btn, _parent = built
    ri = ctx.state.review_image
    ri.delete_scope_wells = {"A01", "B02"}
    from PySide6.QtWidgets import QPushButton
    reset = next(b for b in btn.menu().findChildren(QPushButton)
                 if b.text() == "Reset to this frame")
    reset.click()
    assert ri.delete_scope_wells is None
    assert btn.text().startswith("Delete scope: this frame")


def test_use_plate_selection_arms_selected_wells_without_writing_them(qtbot):
    ctx = _ctx(selected=("B02", "C03", "Z99"))
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = build_delete_scope_control(ctx, parent)
    from PySide6.QtWidgets import QPushButton
    plate = next(b for b in btn.menu().findChildren(QPushButton)
                 if b.text() == "Use plate selection")
    plate.click()
    # Z99 is not a loaded well, so it is filtered out.
    assert ctx.state.review_image.delete_scope_wells == {"B02", "C03"}
    # the app-wide plate selection is untouched
    assert ctx.state.selection.selected_wells == {"B02", "C03", "Z99"}


def test_baseline_timepoint_is_offered_and_seeded(qtbot):
    """A numeric ``t=0`` used to normalise to "" — a blank row in the dropdown
    that the displayed frame could never match, so the popover read "No
    timepoints" for the frame on screen and "All TPs" silently excluded it."""
    ctx = _ctx(tps=(0.0, 24.0), tp="0")
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = build_delete_scope_control(ctx, parent)
    btn.menu().aboutToShow.emit()
    from widgets.checkable_dropdown import CheckableDropdown
    tps_dd = btn.menu().findChildren(CheckableDropdown)[2]
    assert [str(d) for d in tps_dd.checkedData()] == ["0"]
    assert "" not in [tps_dd._data[i] for i in range(tps_dd.count())]


def test_mixed_parseable_and_opaque_well_labels_do_not_break_the_popover(qtbot):
    """A dataset folder can hold ``A01.csv`` beside ``summary_all.csv``.
    Sorting those on ``parse_rc`` alone raises comparing None with str, which
    took every dropdown down with it and left the scope armable but unlabelled."""
    ctx = _ctx(wells=("A01", "B02", "summary_all"))
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = build_delete_scope_control(ctx, parent)
    btn.menu().aboutToShow.emit()
    from widgets.checkable_dropdown import CheckableDropdown
    wells_dd = btn.menu().findChildren(CheckableDropdown)[0]
    labels = [wells_dd._labels[i] for i in range(wells_dd.count())]
    assert labels == ["A01", "B02", "summary_all"]


def test_ticking_one_dropdown_arms_only_that_field(built):
    """Arming wells must not also pin the FOV and timepoint to whatever was on
    screen — the untouched fields keep following the displayed frame, or moving
    to the next FOV would silently put the user outside their own scope."""
    ctx, btn, _parent = built
    btn.menu().aboutToShow.emit()
    from widgets.checkable_dropdown import CheckableDropdown
    dropdowns = btn.menu().findChildren(CheckableDropdown)
    assert len(dropdowns) == 3
    dropdowns[0].setCheckedData(["A01", "B02"])
    ri = ctx.state.review_image
    assert ri.delete_scope_wells == {"A01", "B02"}
    assert ri.delete_scope_fovs is None
    assert ri.delete_scope_tps is None
    assert describe_delete_scope(ctx) == (
        "Delete scope: 2 wells · this FOV · this TP"
    )
