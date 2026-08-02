"""A well in progress is drawn as a dial, not as a solid colour.

The plate map's hue says which stage a well is in. It cannot say how far
through that stage the well is, so a well on its first FOV-group and one on its
last looked identical for however long the well took — which on a timelapse
plate is most of the run. The well is now the dial: dark tone for what is done,
light for what is left, both of them the state's own colour, filled clockwise.

The data was already there. ``RunDashboard`` has recorded per-well
``(done, total)`` FOV-group counts from the pipeline's ``well_fov`` events since
those events existed; it only ever rendered them as a stack of progress bars
below the plate, for the handful of wells active at that moment.

The dial covers transfers too, not only computing: a well uploading its input
zip or downloading its output fills with the bytes moved, so the stages that
dominate a remote run's wall-clock are the ones the plate describes best.

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

    An uploaded well now carries an *empty* dial rather than none at all (see
    ``test_an_uploaded_well_waiting_its_turn_reads_empty``); what must never
    happen is it reading as finished.
    """
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "upload")
    assert _fractions(d._plate)[(3, 6)] == 0.0, "uploaded is not computed"
    d.set_well_stage("D07", "active")
    d.set_well_fov("D07", 3, 12)
    assert _fractions(d._plate)[(3, 6)] == pytest.approx(0.25)


def test_an_uploaded_well_waiting_its_turn_reads_empty(qapp):
    """Started but nothing finished is an empty dial, not a plain disc.

    A well whose zip is on the remote and which has not been looked at yet is
    the single largest block of a mid-run remote plate. Drawn as a solid well
    it was the one part of the map that said nothing about what it was waiting
    for; drawn as an empty dial it says "this one has not begun".
    """
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "upload")
    assert _fractions(d._plate)[(3, 6)] == 0.0


def test_a_grouped_well_is_still_pending_with_no_dial(qapp):
    """Grouping happens on this machine before the run reaches the well at all,
    so a grouped well has entered nothing — it stays a plain pending disc."""
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "group")
    assert (3, 6) not in _fractions(d._plate)


# ── transfers fill the dial too ─────────────────────────────────────────────

def test_an_upload_in_flight_fills_the_dial(qapp):
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_transfer("D07", "up", 0.4)
    assert _fractions(d._plate)[(3, 6)] == pytest.approx(0.4)


def test_a_finished_upload_hands_the_dial_back_to_the_stages(qapp):
    """1.0 ends the transfer rather than pinning the dial full.

    The well is marked ``upload`` as rsync *starts* sending it, so a completed
    upload left in place would hold a full dial on a well that has not begun
    computing — the exact reading this feature exists to prevent.
    """
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "upload")
    d.set_well_transfer("D07", "up", 0.4)
    assert _fractions(d._plate)[(3, 6)] == pytest.approx(0.4)
    d.set_well_transfer("D07", "up", 1.0)
    assert _fractions(d._plate)[(3, 6)] == 0.0, "uploaded, waiting to compute"


def test_a_download_in_flight_outranks_having_computed(qapp):
    """A well being fetched has by definition already computed, so the compute
    stage would otherwise pin it full for the whole of the fetch — which on a
    slow link is the longest single wait in the run.

    The closing 1.0 used to end the transfer and return the well to the compute
    stage's full ring. That was reported from a real run: the well "started
    blue, then a pie that filled to blue, then stayed at blue despite the top
    progress bar stating that wells were actively downloading". The zip is only
    the first of two transfers — the CSVs follow, unmeasured — so the ring now
    freezes at the last measured fraction until the well is marked ``download``.
    """
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "upload")
    d.set_well_stage("D07", "process")
    assert _fractions(d._plate)[(3, 6)] == 1.0
    d.set_well_transfer("D07", "down", 0.25)
    assert _fractions(d._plate)[(3, 6)] == pytest.approx(0.25)
    d.set_well_transfer("D07", "down", 1.0)
    assert _fractions(d._plate)[(3, 6)] == pytest.approx(0.25), (
        "the zip finished but the CSVs have not — the ring must not refill, "
        "which would be indistinguishable from 'computed, not yet fetching'")
    d.set_well_stage("D07", "download")
    assert _fractions(d._plate)[(3, 6)] == 1.0, "fetched"


def test_an_upload_does_not_speak_for_a_well_that_has_moved_on(qapp):
    """A stale upload sample arriving late must not un-fill a computing well."""
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "upload")
    d.set_well_stage("D07", "active")
    d.set_well_fov("D07", 6, 12)
    d.set_well_transfer("D07", "up", 0.1)
    assert _fractions(d._plate)[(3, 6)] == pytest.approx(0.5)


def test_an_unknown_transfer_direction_is_ignored(qapp):
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_transfer("D07", "sideways", 0.5)
    assert (3, 6) not in _fractions(d._plate)


def test_a_reset_clears_transfers_too(qapp):
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_transfer("D07", "down", 0.5)
    d.reset(remote=True)
    assert _fractions(d._plate) == {} and d._xfer == {}


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


def _centre_luminance(grid, row: int, col: int) -> float:
    """Perceived brightness at the middle of one painted well, 0..255."""
    img = grid.grab().toImage()
    centre = grid._well_rect(row, col).center()
    c = img.pixelColor(int(centre.x()), int(centre.y()))
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def test_the_completed_share_is_the_dark_one(qapp):
    """Light is what is left to do; dark is what is done.

    Asserted off the rendered pixels rather than off the brush calls, because
    which tone means which is a question about what the user sees, and every
    defect this feature has had was invisible to a test that only checked the
    numbers going in.
    """
    plate = WellPlateSelector()
    plate._grid.resize(360, 240)
    plate.setWellColors({"A01": "#3d7fd6", "A02": "#3d7fd6"})
    plate.setWellFraction("A01", 0.0)          # nothing done
    plate.setWellFraction("A02", 1.0)          # all done
    empty = _centre_luminance(plate._grid, 0, 0)
    full = _centre_luminance(plate._grid, 0, 1)
    assert empty > full, (
        f"an untouched dial ({empty:.0f}) must read lighter than a finished "
        f"one ({full:.0f}) — the tones are the wrong way round"
    )


def test_the_plate_still_paints_with_a_dial_on_it(qapp):
    """Cheap smoke over the paint path — a raise here is a blank dashboard."""
    d = RunDashboard()
    d.resize(400, 320)
    d.reset()
    for i, well in enumerate(("A01", "A02", "A03", "A04")):
        d.set_well_stage(well, "active")
        d.set_well_fov(well, i, 3)
    d._plate.grab()          # would raise if paintEvent blew up
