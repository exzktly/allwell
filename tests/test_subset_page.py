"""Offscreen controller/page tests for the Subset / Split Dataset feature.

Same pattern as ``tests/test_validate_page.py``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from tests.fixtures.dataset_builder import build_dataset  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _run_worker_to_completion(ctrl) -> None:
    """See tests/test_validate_page.py for why isRunning() polling is unsafe."""
    worker = ctrl._worker
    assert worker is not None
    worker.wait(5000)
    for _ in range(10):
        QApplication.processEvents()


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
    ctrl.shutdown()
