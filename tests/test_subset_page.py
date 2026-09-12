"""Offscreen controller/page tests for the Subset / Split Dataset feature.

Same pattern as ``tests/test_validate_page.py``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea

from tests._datasets_worker import run_worker_to_completion  # noqa: E402

from tests.fixtures.dataset_builder import build_dataset  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# One shared implementation — the six copies of this helper all failed
# open the same two ways. See tests/_datasets_worker.py.
_run_worker_to_completion = run_worker_to_completion


def test_readout_seeds_wells_timepoints_fovs(qapp, tmp_path):
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h", "24h"], fovs=("F001", "F002"))

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()

    assert set(page.selected_wells()) == {"A01", "B02"}
    assert set(page.selected_timepoints()) == {"0h", "24h"}
    assert set(page.selected_fovs()) == {"F001", "F002"}
    ctrl.shutdown()


def test_timepoints_and_fovs_show_a_pre_source_hint(qapp, tmp_path):
    """Regression guard (UX audit P2-9): the Timepoints/FOVs checkbox rows
    were just blank space under their headings before a source was picked —
    Import Loose's equivalent readout already says "Pick a folder to
    preview." in the same situation; Subset said nothing at all."""
    from datasets.subset.controller import SubsetController

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    assert not page._tp_hint.isHidden()
    assert not page._fov_hint.isHidden()

    src = tmp_path / "src"
    build_dataset(src, wells=["A01"], timepoints=["0h"], fovs=("F001",))
    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert page._tp_hint.isHidden()
    assert page._fov_hint.isHidden()
    ctrl.shutdown()


def test_selected_wells_get_a_uniform_pre_run_tint(qapp, tmp_path):
    """Regression guard (UX audit P1-1): the picker's own default "selected"
    rendering cycles a rank-based rainbow trace colour per selection order
    (WellPlateSelector's generic multi-select look) -- distracting on its
    own, and on this specific page it also risked reading as one of the
    real post-run state colours (well_color("empty") is theme.Colors.accent,
    the exact hue an unselected-but-enabled well already renders as a faint
    default tint). Every selected well must instead get the same single
    accent_dim tint, and an unselected well must get none."""
    from ui import theme
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    build_dataset(src, wells=["A01", "B02", "C03"], timepoints=["0h"])

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()

    page._plate.setSelectedWellIds(["A01"])
    colors = dict(page._plate._grid._colors)
    from PySide6.QtGui import QColor
    from widgets.well_plate_selector import _parse_well_id
    a01 = _parse_well_id("A01")
    b02 = _parse_well_id("B02")
    assert colors.get(a01) == QColor(theme.Colors.accent_dim)
    assert colors.get(b02) is None
    ctrl.shutdown()


def test_run_gate_requires_destination_and_selection(qapp, tmp_path):
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    build_dataset(src, wells=["A01"], timepoints=["0h"])
    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert not page._run_btn.isEnabled()

    page._dest_field.set_path(str(tmp_path / "dest"))
    page._refresh_run_enabled()
    assert page._run_btn.isEnabled()
    ctrl.shutdown()


def test_destination_field_flags_a_folder_that_already_holds_a_dataset(qapp, tmp_path):
    """Regression guard (UX audit P1-10): Subset's destination field was the
    only destination picker in the pane with no validator -- pointing it at
    a folder that already holds a finished dataset (subset_compute.py's own
    build_subset_plan already refuses this at plan time) just left the
    Subset button silently disabled with no inline reason."""
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    build_dataset(src, wells=["A01"], timepoints=["0h"])
    existing_dest = tmp_path / "dest"
    build_dataset(existing_dest, wells=["B02"], timepoints=["0h"])

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(existing_dest))
    page._refresh_run_enabled()

    assert not page._run_btn.isEnabled()
    assert page._dest_field.message() == "Already holds a finished dataset — choose an empty folder."
    ctrl.shutdown()


def test_subset_run_end_to_end_through_the_page(qapp, tmp_path):
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h", "24h"], fovs=("F001",))

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    # Keep only A01 and only the 0h timepoint.
    page._plate.setSelectedWellIds(["A01"])
    for label, cb in page._tp_checks.items():
        cb.setChecked(label == "0h")

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    assert (dest / "out" / "measurements_A01.csv").exists()
    assert not (dest / "out" / "measurements_B02.csv").exists()
    assert not page._open_btn.isHidden()
    # Regression guard (UX audit P2-3): the dashboard headline shows a
    # basename, not the full destination path buried in the raw summary
    # ("Subset written to <dest_root> (...)").
    assert str(dest) not in page.dashboard._headline.text()
    assert dest.name in page.dashboard._headline.text()
    assert str(dest) in page.dashboard._headline.toolTip()
    ctrl.shutdown()


def test_page_content_is_wrapped_in_a_scroll_area(qapp):
    """Regression guard: the page used to lay every section directly into
    its own top-level QVBoxLayout, so at the app's default window height
    (1640x980 - allwell.py) the dashboard's plate map at the bottom got
    crushed into whatever space was left instead of the page scrolling to
    it. set_path()/set_source_path() etc. must still resolve on widgets
    reparented under the scroll area's inner content widget - Qt's
    layout.addWidget() reparents regardless of constructor parent, so this
    also guards that the reparenting didn't break anything."""
    from datasets.subset.controller import SubsetController

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    # The page's own top-level layout wraps everything in one QScrollArea.
    # Check it via the outer layout directly rather than findChildren() +
    # a count, which would be one more assumption to keep in sync with
    # whatever else on the page happens to contain a QScrollArea.
    outer_scroll = page.layout().itemAt(0).widget()
    assert isinstance(outer_scroll, QScrollArea)
    assert outer_scroll.widgetResizable() is True
    assert page._src_field.parent() is not page  # reparented under the scroll content
    ctrl.shutdown()


def test_dashboard_does_not_stretch_to_fill_a_tall_viewport(qapp):
    """Regression guard: the scroll area's content widget gets resized to
    fill the viewport (QScrollArea.setWidgetResizable(True)), and with the
    raw log hidden by default the dashboard used to be the only widget in
    the page's outer layout willing to absorb that leftover height — which
    its own internal ``center.addStretch(1)`` then turned into a growing
    dead zone of blank space below the height-capped plate map, rather than
    the plate map itself growing. A trailing ``outer.addStretch(1)`` after
    the page's last real widget should collect that leftover space instead,
    so the dashboard renders at its natural, tight sizeHint regardless of
    how much taller the window is than the page's actual content."""
    from datasets.subset.controller import SubsetController

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page.resize(1300, 2000)  # much taller than the page's natural content
    page.show()
    qapp.processEvents()
    assert page.dashboard.height() <= page.dashboard.sizeHint().height() + 20
    ctrl.shutdown()


def test_dashboard_has_no_plate_map_of_its_own(qapp):
    """Regression guard: this page used to show two plate maps — the
    interactive well-picker at the top and a second, separate status plate
    inside SubsetDashboard at the bottom — which read as inconsistent,
    duplicated UI. SubsetDashboard is built with PlateProgress's
    show_plate=False and keeps only the headline/bar/rail/legend chrome;
    progress is painted directly onto the picker instead (see the
    overlay test below)."""
    from datasets.subset.controller import SubsetController

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    assert not hasattr(page.dashboard, "_plate")
    ctrl.shutdown()


def test_picker_plate_renders_at_the_same_cell_size_as_a_dashboard_plate(qapp):
    """Regression guard (UX audit P2-5): the picker plate keeps its own
    All/Invert/Clear action row, which the dashboard's status plate hides. If
    both plates simply shared one maximum height, that row would eat room the
    grid would otherwise get and the same cap would produce a visibly smaller
    grid here than on every other plate map in the pane.
    ``widgets.plate_geometry.apply_plate_geometry`` is what keeps them equal: it
    measures the action row and adds its height on top of the shared cap for the
    ``"picker"`` role, so the *grid* budget is the same either way.

    Neither plate is resized by hand here — the point is that the geometry
    helper's own caps are what make the two agree, so the test lets each plate
    take the size its real page gives it. (It used to resize the picker to a
    hardcoded 330x258, the empirically-matched pair those caps replaced; that
    assertion could only ever confirm the numbers the test itself supplied.)"""
    from datasets.concatenate.dashboard import ConcatDashboard
    from datasets.subset.controller import SubsetController

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page.resize(1300, 980)  # generous, so the picker reaches its own caps
    page.show()
    qapp.processEvents()
    qapp.processEvents()
    _lab, picker_cell, _ox, _oy = page._plate._grid._metrics()

    dashboard = ConcatDashboard()
    dashboard.resize(1300, 600)  # generous height so the plate reaches its own cap
    dashboard.show()
    qapp.processEvents()
    qapp.processEvents()
    _lab2, dashboard_cell, _ox2, _oy2 = dashboard._plate._grid._metrics()

    assert abs(picker_cell - dashboard_cell) < 0.5, (
        f"picker cell {picker_cell}px vs dashboard cell {dashboard_cell}px "
        "-- the two plate maps no longer render at the same scale"
    )
    ctrl.shutdown()


def test_every_plate_map_is_horizontally_centred(qapp):
    """The picker and a dashboard status plate must both sit centred in their
    own container. They used to be added with ``Qt.AlignLeft`` — on a page much
    wider than the plate's own width cap that pinned each one to the left edge
    of a different-width column, so two controls that are the same size read as
    misaligned with each other."""
    from datasets.concatenate.dashboard import ConcatDashboard
    from datasets.subset.controller import SubsetController

    def _gaps(plate):
        container = plate.parentWidget()
        left = plate.geometry().x()
        right = container.width() - (plate.geometry().x() + plate.width())
        return left, right

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page.resize(1300, 980)
    page.show()
    qapp.processEvents()
    qapp.processEvents()
    left, right = _gaps(page._plate)
    assert abs(left - right) <= 1, f"subset picker not centred: {left} vs {right}"

    dashboard = ConcatDashboard()
    dashboard.resize(1300, 600)
    dashboard.show()
    qapp.processEvents()
    qapp.processEvents()
    left, right = _gaps(dashboard._plate)
    assert abs(left - right) <= 1, f"dashboard status plate not centred: {left} vs {right}"
    ctrl.shutdown()


def test_dashboard_hidden_until_a_run_starts(qapp, tmp_path):
    """Regression guard: the status strip below the Run button used to be
    visible from the moment the page loaded, showing an idle headline over
    an empty progress bar before anything had happened — confusing, since
    it looks like a run is in some in-between state rather than simply not
    started yet. It should stay hidden until Run is actually pressed, and
    hide again if the source dataset changes (any prior run's status is no
    longer meaningful for a different dataset)."""
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h"])

    # isHidden() (an explicit local hide/show flag), not isVisible() — the
    # page is never actually .show()n in this test, so isVisible() would
    # report False for the whole tree regardless of these setVisible() calls.
    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    assert page.dashboard.isHidden() is True

    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert page.dashboard.isHidden() is True  # still nothing has run

    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()
    page.runRequested.emit()
    assert page.dashboard.isHidden() is False  # shown the moment the run starts
    _run_worker_to_completion(ctrl)
    assert page.dashboard.isHidden() is False  # stays up to show the result

    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert page.dashboard.isHidden() is True  # hidden again for the new selection
    ctrl.shutdown()


def test_run_progress_overlays_onto_the_picker_plate(qapp, tmp_path):
    """The core of the merged-plate design: per-well progress from a real
    run lands as colour on the *same* WellPlateSelector the user picked
    wells with, not a separate widget. Drives a real Subset run end to end
    and inspects the picker's own colour map."""
    from widgets.well_plate_selector import _parse_well_id
    from datasets.subset.controller import SubsetController
    from datasets.subset.dashboard import well_color

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h"])

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    assert page._plate.isEnabled() is True
    page.runRequested.emit()
    assert page._plate.isEnabled() is False  # locked for the duration of the run
    _run_worker_to_completion(ctrl)
    assert page._plate.isEnabled() is True  # unlocked once it's finished

    grid = page._plate._grid
    for well in ("A01", "B02"):
        key = _parse_well_id(well)
        assert grid._colors.get(key) is not None
        assert grid._colors[key].name() == well_color("done").lower()
        # Regression guard (UX audit P2-12): "done" painted colour only --
        # accessibility-unreadable to anyone who can't distinguish
        # success-green from the other states' colours.
        assert grid._markers.get(key) == "✓"
        # ...and a copied well ends as a full dial, not a bare disc: the
        # picker plate is where this page's per-well progress is drawn, so a
        # run that finished must leave it saying so.
        assert grid._fractions.get(key) == 1.0
    ctrl.shutdown()


def test_per_well_progress_fills_a_dial_on_the_picker_plate(qapp, tmp_path):
    """The sibling Datasets controllers hand their worker's ``wellFraction``
    signal to ``dashboard.set_well_progress``. Subset's dashboard has no plate
    of its own (``PlateProgress(show_plate=False)``), so that call lands on
    ``PlateProgress.set_well_fraction``'s documented ``self._plate is None``
    no-op and every fraction the worker computes is silently discarded — the
    one tool that emits per-well sub-progress and rendered none of it. The
    fractions must reach the picker plate the page actually shows."""
    from widgets.well_plate_selector import _parse_well_id
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    build_dataset(src, wells=["A01"], timepoints=["0h"])

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()

    grid = page._plate._grid
    key = _parse_well_id("A01")
    ctrl._on_well_fraction("A01", 2, 4)
    assert grid._fractions.get(key) == 0.5
    ctrl._on_well_fraction("A01", 4, 4)
    assert grid._fractions.get(key) == 1.0
    ctrl.shutdown()


def test_an_uncounted_well_gets_no_dial_rather_than_an_empty_one(qapp, tmp_path):
    """A caller that has not counted the work yet is reporting "unknown", and
    unknown must not render as a definite zero-filled dial — that is the same
    picture as a well that has genuinely done nothing
    (``widgets.plate_progress.fraction_or_none``)."""
    from widgets.well_plate_selector import _parse_well_id
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    build_dataset(src, wells=["A01"], timepoints=["0h"])

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()

    ctrl._on_well_fraction("A01", 0, 0)
    assert page._plate._grid._fractions.get(_parse_well_id("A01")) is None
    ctrl.shutdown()


def test_a_copying_well_carries_a_dial_and_no_glyph(qapp, tmp_path):
    """A well being copied is drawn as a dial, so it carries no in-flight
    glyph: the "⚙" it used to wear sat in the middle of exactly the area the
    dial fills, and the fill plus the colour already say the well is working.
    Terminal states keep their markers — those have no dial competing for the
    space, and colour alone is unreadable to anyone who can't distinguish
    success-green from accent-lilac."""
    from widgets.well_plate_selector import _parse_well_id
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h"])

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()

    grid = page._plate._grid
    ctrl._on_well_stage("A01", "copying")
    assert grid._markers.get(_parse_well_id("A01")) is None
    ctrl._on_well_stage("A01", "done")
    assert grid._markers.get(_parse_well_id("A01")) == "✓"
    ctrl.shutdown()


def test_a_terminal_stage_settles_the_dial(qapp, tmp_path):
    """Neither terminal state is left to whatever the last sub-progress tick
    happened to be: a copied well reads as a full circle, and a well nothing
    matched has no copy progress to describe at all — a part-filled dial
    beside its "!" would suggest it is still going."""
    from widgets.well_plate_selector import _parse_well_id
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h"])

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()

    grid = page._plate._grid
    ctrl._on_well_fraction("A01", 1, 4)
    ctrl._on_well_stage("A01", "done")
    assert grid._fractions.get(_parse_well_id("A01")) == 1.0

    ctrl._on_well_fraction("B02", 1, 4)
    ctrl._on_well_stage("B02", "empty")
    assert grid._fractions.get(_parse_well_id("B02")) is None
    ctrl.shutdown()


def test_a_second_run_does_not_inherit_the_first_run_s_dials(qapp, tmp_path):
    """``reset`` clearing colours and markers but not fractions is exactly the
    bug a test caught on the run dashboard when the dial first shipped: every
    second run would start wearing the first run's progress."""
    from widgets.well_plate_selector import _parse_well_id
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h"])

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)
    assert page._plate._grid._fractions.get(_parse_well_id("A01")) == 1.0

    # A fresh source wipes the plate, dials included.
    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert page._plate._grid._fractions == {}
    ctrl.shutdown()


def test_changing_the_selection_after_a_run_clears_the_dials(qapp, tmp_path):
    """The plate goes interactive again when the run ends, and every copied
    well is a full circle. Without clearing them, the pre-run ``accent_dim``
    tint would be painted underneath a plate still wearing the last run's
    progress."""
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h"])

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)
    assert page._plate._grid._fractions != {}

    page._plate.setSelectedWellIds(["A01"])
    assert page._plate._grid._fractions == {}
    ctrl.shutdown()


def test_folder_mode_source_fails_the_run_not_the_field(qapp, tmp_path):
    """Regression-gap guard: SubsetPage's own source-field validator
    (``_is_dataset`` in datasets/subset/view.py) only checks for an ``in``
    and an ``out`` directory -- it has no ``has_folder_mode_output`` check,
    unlike ``build_subset_plan`` (subset_compute.py, guarded at the compute
    layer by test_subset_compute.py::test_folder_mode_source_refused). So a
    folder-mode source (``out/<WELL>/`` QC images alongside the zip) reads as
    a perfectly valid source field, the Run button enables, and the refusal
    only ever happens inside SubsetWorker.run() once Run is actually pressed.
    Nothing previously drove that failure through the real controller/worker
    to confirm it actually surfaces on the page — this exercises the whole
    field-looks-fine-but-the-run-fails path end to end: the run must fail
    with SubsetError's own "per-well folders" message landing in both the log
    and the dashboard's failed state, and the source/destination fields must
    unlock again afterwards rather than staying stuck mid-run."""
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h"])
    (src / "out" / "A01").mkdir()  # folder-mode QC output alongside the zip

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    # The field itself sees nothing wrong -- confirms the gap this test closes.
    assert page._src_field.is_valid() is True
    assert page._run_btn.isEnabled() is True

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    assert "per-well folders" in page._status.toPlainText()
    assert "Failed" in page.dashboard._headline.text()
    assert "per-well folders" in page.dashboard._headline.text()
    assert not (dest / "out" / "pipeline_info.json").exists()
    # Fields unlock again once the failed run finishes.
    assert page._src_field.isEnabled() is True
    assert page._dest_field.isEnabled() is True
    assert page._src_field._browse_btn.isEnabled() is True
    assert page._dest_field._browse_btn.isEnabled() is True
    ctrl.shutdown()


def test_source_and_destination_fields_are_locked_during_a_run(qapp, tmp_path):
    """Regression guard: only the plate and Run/Queue buttons used to be
    locked during a run — the source field stayed clickable. Picking a
    different source mid-run fires sourceChanged, which repaints the picker
    plate for the newly-chosen dataset while the old run's SubsetWorker
    keeps executing in the background; its still-arriving wellStage signals
    would then paint the *previous* dataset's progress onto wells the user
    now believes belong to the new, unrelated dataset. Locking the fields
    the same way the plate already is (SubsetController._launch) closes off
    that path through the GUI entirely."""
    from datasets.subset.controller import SubsetController

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h"])

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    # Locked means the Browse button, not the whole field (UX audit P2-4) —
    # the path text is already read-only and stays at full contrast so a
    # user watching a long run can still read what it's running against.
    assert page._src_field._browse_btn.isEnabled() is True
    assert page._dest_field._browse_btn.isEnabled() is True
    page.runRequested.emit()
    assert page._src_field.isEnabled() is True
    assert page._dest_field.isEnabled() is True
    assert page._src_field._browse_btn.isEnabled() is False
    assert page._dest_field._browse_btn.isEnabled() is False
    _run_worker_to_completion(ctrl)
    assert page._src_field._browse_btn.isEnabled() is True
    assert page._dest_field._browse_btn.isEnabled() is True
    ctrl.shutdown()
