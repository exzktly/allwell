"""A well in progress is drawn as a dial, not as a solid colour.

The plate map's hue says which stage a well reached. It cannot say how far
through that stage the well is, so a well on its first FOV-group and one on its
last looked identical for however long the well took — which on a timelapse
plate is most of the run. The well is now the dial: light tone for what is done,
dark for what is left, both of them the state's own colour, filled clockwise.

The data was already there. ``RunDashboard`` has recorded per-well
``(done, total)`` FOV-group counts from the pipeline's ``well_fov`` events since
those events existed; it only ever rendered them as a stack of progress bars
below the plate, for the handful of wells active at that moment.

``setWellFraction`` is opt-in, and nothing but the run dashboard sets it — so
Review's rail, the heat-map drag source and the replicate pickers all paint
exactly as they did.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from widgets.run_dashboard import RunDashboard
from widgets.well_plate_selector import WellPlateSelector


def _fractions(plate: WellPlateSelector) -> dict:
    return dict(plate._grid._fractions)


def test_a_plate_starts_with_no_dials(qapp):
    plate = WellPlateSelector()
    assert _fractions(plate) == {}


def test_setting_a_fraction_records_it(qapp):
    plate = WellPlateSelector()
    plate.setWellFraction("C03", 0.25)
    assert _fractions(plate) == {(2, 2): 0.25}


def test_a_fraction_is_clamped_to_the_dial(qapp):
    plate = WellPlateSelector()
    plate.setWellFraction("A01", 4.2)
    plate.setWellFraction("A02", -1.0)
    assert _fractions(plate)[(0, 0)] == 1.0
    assert _fractions(plate)[(0, 1)] == 0.0


def test_none_removes_the_dial(qapp):
    plate = WellPlateSelector()
    plate.setWellFraction("C03", 0.5)
    plate.setWellFraction("C03", None)
    assert _fractions(plate) == {}


def test_an_unparseable_well_is_ignored(qapp):
    plate = WellPlateSelector()
    plate.setWellFraction("not-a-well", 0.5)
    assert _fractions(plate) == {}


def test_fov_progress_reaches_the_plate(qapp):
    """The wiring: the counts the dashboard already had now drive the dial."""
    d = RunDashboard()
    d.reset()
    d.set_well_stage("B04", "active")
    d.set_well_fov("B04", 3, 12)
    assert _fractions(d._plate)[(1, 3)] == pytest.approx(0.25)


def test_a_finished_well_reads_full(qapp):
    d = RunDashboard()
    d.reset()
    d.set_well_stage("B04", "active")
    d.set_well_fov("B04", 3, 12)
    d.set_well_stage("B04", "process")
    assert _fractions(d._plate)[(1, 3)] == 1.0


def test_an_active_well_with_no_counts_yet_reads_empty(qapp):
    """It has started, so it is a dial — an empty one — not a pending well."""
    d = RunDashboard()
    d.reset()
    d.set_well_stage("B04", "active")
    assert _fractions(d._plate)[(1, 3)] == 0.0


def test_uploading_a_well_does_not_fill_its_dial(qapp):
    """The bug rendering the dashboard caught, and unit tests did not.

    On a remote run every well reaches ``upload`` long before it computes. An
    earlier version treated any completed stage as done, so the whole plate
    showed full dials from the moment the zips landed — the feature did nothing
    precisely where the wait is longest. The tests all passed because they
    drove ``active``/``process`` directly and never sent an ``upload``.
    """
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "upload")
    assert (3, 6) not in _fractions(d._plate), "uploaded is not started"
    d.set_well_stage("D07", "active")
    d.set_well_fov("D07", 3, 12)
    assert _fractions(d._plate)[(3, 6)] == pytest.approx(0.25)


def test_a_well_that_finished_short_still_reads_full(qapp):
    """Groups can fail, so the last count is not always total/total.

    Leaving the dial at 3/12 would read as still-running forever. The shortfall
    is reported as a partial well by the run's completeness record, which is
    where it belongs.
    """
    d = RunDashboard()
    d.reset()
    d.set_well_stage("B04", "active")
    d.set_well_fov("B04", 3, 12)
    d.set_well_stage("B04", "process")
    assert _fractions(d._plate)[(1, 3)] == 1.0


def test_a_downloaded_well_reads_full(qapp):
    """Remote's terminal stage fills the dial like local's ``process`` does."""
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "upload")
    d.set_well_stage("D07", "download")
    assert _fractions(d._plate)[(3, 6)] == 1.0


def test_an_errored_well_has_no_dial(qapp):
    """A part-filled ring beside the ✕ would suggest it is still going."""
    d = RunDashboard()
    d.reset()
    d.set_well_stage("B04", "active")
    d.set_well_fov("B04", 3, 12)
    d.set_well_stage("B04", "error")
    assert (1, 3) not in _fractions(d._plate)


def test_a_reset_clears_the_dials(qapp):
    d = RunDashboard()
    d.reset()
    d.set_well_stage("B04", "active")
    d.set_well_fov("B04", 3, 12)
    d.reset()
    assert _fractions(d._plate) == {}


def test_the_plate_still_paints_with_a_dial_on_it(qapp):
    """Cheap smoke over the paint path — a raise here is a blank dashboard."""
    d = RunDashboard()
    d.resize(400, 320)
    d.reset()
    for i, well in enumerate(("A01", "A02", "A03", "A04")):
        d.set_well_stage(well, "active")
        d.set_well_fov(well, i, 3)
    d._plate.grab()          # would raise if paintEvent blew up
