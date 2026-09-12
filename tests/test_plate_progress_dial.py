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

from PySide6.QtGui import QColor  # noqa: E402

from ui import theme  # noqa: E402
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


def test_an_uploaded_well_holds_a_full_dial_until_it_computes(qapp):
    """The dial must never run backwards at the upload → compute handover.

    The dial spends the whole upload filling with the bytes sent, and
    ``set_well_transfer`` drops the transfer entry the moment it reaches 1.0.
    Reading the uploaded well as an *empty* dial therefore emptied it at the
    exact instant its upload completed, which on screen looks like the transfer
    undoing itself. Its archive is on the remote in full; what it waits for now
    is its turn, not more transfer.

    It empties when the well genuinely starts computing — that zero is true —
    and fills again with FOV-groups from there.
    """
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "upload")
    assert _fractions(d._plate)[(3, 6)] == 1.0, "uploaded in full"
    d.set_well_stage("D07", "active")
    assert _fractions(d._plate)[(3, 6)] == 0.0, "computing, nothing measured yet"
    d.set_well_fov("D07", 3, 12)
    assert _fractions(d._plate)[(3, 6)] == pytest.approx(0.25)


def test_a_full_dial_on_an_uploaded_well_is_told_apart_by_colour(qapp):
    """A full dial now means "uploaded" or "computed" depending on the well's
    colour — the accepted cost of never running backwards. The two must at
    least not be the same colour."""
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "upload")
    d.set_well_stage("E07", "process")
    assert _fractions(d._plate)[(3, 6)] == 1.0
    assert _fractions(d._plate)[(4, 6)] == 1.0
    assert d._well_color("D07") != d._well_color("E07")


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
    """1.0 ends the *transfer* rather than pinning the dial through it.

    The transfer entry is dropped so later stages decide the dial — but the
    well is marked ``upload`` as rsync *starts* sending it, so the stage it
    hands back to is "uploaded", which is itself full. The fill is therefore
    continuous across the handover: it climbs with the bytes and stays there.
    """
    d = RunDashboard()
    d.reset(remote=True)
    d.set_well_stage("D07", "upload")
    d.set_well_transfer("D07", "up", 0.4)
    assert _fractions(d._plate)[(3, 6)] == pytest.approx(0.4)
    d.set_well_transfer("D07", "up", 1.0)
    assert d._xfer == {}, "the transfer is over"
    assert _fractions(d._plate)[(3, 6)] == 1.0, "and the dial does not rewind"


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


def _centre_colour(grid, row: int, col: int):
    img = grid.grab().toImage()
    centre = grid._well_rect(row, col).center()
    return img.pixelColor(int(centre.x()), int(centre.y()))


@pytest.mark.parametrize("token", ["accent2", "accent", "warn", "success"])
def test_an_unfilled_dial_keeps_the_state_colour(qapp, token):
    """The unfilled share must stay *that state's* colour, not go white.

    Driven with the real tokens the app paints dials in, because the earlier
    version of this test supplied its own ``#3d7fd6`` — a mid-value blue that
    `QColor.lighter(165)` leaves pale-blue. Every real state colour sits at
    value 245-253, where ``lighter`` stops raising value and starts dropping
    *saturation*: ``accent2 #B98CF7`` came back as literally ``#ffffff``, so an
    uploaded well on a remote run rendered as a white disc with no hue left to
    say which stage it was in. The old test passed throughout, because it only
    asked whether the unfilled share was *brighter* — and white is the
    brightest thing there is.
    """
    from ui import theme

    base = QColor(getattr(theme.Colors, token))
    plate = WellPlateSelector()
    plate._grid.resize(360, 240)
    plate.setWellColors({"A01": base.name()})
    plate.setWellFraction("A01", 0.0)          # nothing done: all unfilled tone
    px = _centre_colour(plate._grid, 0, 0)

    assert px.saturation() > 0, (
        f"{token} {base.name()} rendered its unfilled dial at "
        f"{px.name()} with no saturation — the hue is gone"
    )
    hue_gap = abs(px.hue() - base.hue())
    hue_gap = min(hue_gap, 360 - hue_gap)
    assert hue_gap <= 12, (
        f"{token} unfilled dial drifted hue {base.hue()}° -> {px.hue()}°"
    )


def test_the_completed_share_is_the_dark_one(qapp):
    """Light is what is left to do; dark is what is done.

    Asserted off the rendered pixels rather than off the brush calls, because
    which tone means which is a question about what the user sees, and every
    defect this feature has had was invisible to a test that only checked the
    numbers going in.
    """
    plate = WellPlateSelector()
    plate._grid.resize(360, 240)
    plate.setWellColors({"A01": theme.Colors.accent2, "A02": theme.Colors.accent2})
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


# ── the two halves have to be tellable apart ────────────────────────────────
#
# Reported: "a recent change altered the saturation of the completed portion of
# the pie … it's too light now and I can't see the difference between complete
# and incomplete portions."
#
# The cause was 68b1690, which gave each half its own sphere gradient. The
# gradient's brightest stop blends 35% toward white, and blending toward white
# compresses two tones *together* — so over the upper-left of every well the
# done wedge was both paler and less distinguishable from the remainder. Green
# was worst: a centre contrast of 39 luminance fell to 25 at the highlight.
#
# Nothing in the suite noticed, because the dial had only ever been asserted on
# its fractions, never on the tones it draws them in. These assert the tones.

_DIAL_STATES = ("accent", "success", "warn", "accent2", "text_muted", "danger")


def _luma(c) -> float:
    return 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()


def _boundary_contrasts(state: str) -> list[float]:
    """Contrast across the wedge boundary at each stop of the dial's gradient.

    Adjacent pixels either side of the boundary sit at the *same* position in
    the gradient, so the pair to compare is the done tone and the remaining
    tone transformed identically — not the highlight of one against the shadow
    of the other.
    """
    from widgets.well_plate_selector import _PlateGrid

    base = QColor(getattr(theme.Colors, state))
    left = _PlateGrid._remaining_tone(base)
    return [abs(_luma(d) - _luma(r)) for d, r in zip(
        _PlateGrid._sphere_stops(base, _PlateGrid._DIAL_RELIEF),
        _PlateGrid._sphere_stops(left, _PlateGrid._DIAL_RELIEF),
    )]


def test_the_two_halves_stay_apart_at_every_point_of_the_dial(qapp):
    """Including under the highlight, which is where they collapsed."""
    for state in _DIAL_STATES:
        worst = min(_boundary_contrasts(state))
        assert worst >= 30.0, (
            f"{state}: the done and remaining halves come within {worst:.1f} "
            f"luminance of each other somewhere on the dial")


def test_the_done_half_is_never_the_lighter_of_the_two(qapp):
    """Dark is done, light is left — at every point, not on average."""
    from widgets.well_plate_selector import _PlateGrid

    for state in _DIAL_STATES:
        base = QColor(getattr(theme.Colors, state))
        left = _PlateGrid._remaining_tone(base)
        for done, remaining in zip(
            _PlateGrid._sphere_stops(base, _PlateGrid._DIAL_RELIEF),
            _PlateGrid._sphere_stops(left, _PlateGrid._DIAL_RELIEF),
        ):
            assert _luma(done) < _luma(remaining), state


def test_a_plain_well_keeps_its_full_relief(qapp):
    """The asymmetry is deliberate and worth pinning.

    A plain lit well has no internal boundary for the highlight to compress, so
    its shading costs nothing and stays. Only the dial gives relief up, because
    only the dial is carrying a second signal in the same pixels.
    """
    from widgets.well_plate_selector import _PlateGrid

    assert _PlateGrid._DIAL_RELIEF < 1.0
    full = _PlateGrid._sphere_stops(QColor(theme.Colors.accent), 1.0)
    damped = _PlateGrid._sphere_stops(QColor(theme.Colors.accent),
                                      _PlateGrid._DIAL_RELIEF)
    assert _luma(full[0]) > _luma(damped[0]), "the dial's highlight is gentler"
    assert _luma(full[-1]) < _luma(damped[-1]), "and so is its shadow"
