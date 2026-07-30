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
    # The page's own top-level layout wraps everything in one QScrollArea;
    # a second, unrelated one lives inside SubsetDashboard (PlateProgress's
    # own _fov_scroll, for per-well sub-progress rows) - findChildren() sees
    # both, so check the page's outer layout directly rather than counting.
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


def test_picker_and_dashboard_plates_are_sized_identically(qapp):
    """Regression guard: the interactive well-picker plate at the top of
    this page and SubsetDashboard's own status plate at the bottom used to
    render at visibly different scales — the picker spanned nearly the full
    page width with a small grid centred in a lot of dead space, while the
    dashboard's plate was boxed into a narrower column. Both should be
    capped at the same size so they read as the same kind of control."""
    from datasets.subset.controller import SubsetController

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    assert page._plate.maximumWidth() == page.dashboard._plate.maximumWidth()
    assert page._plate.maximumHeight() == page.dashboard._plate.maximumHeight()
    ctrl.shutdown()
