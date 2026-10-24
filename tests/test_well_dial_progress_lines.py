"""The lines Analyze's run reader turns into a well's progress dial on the plate.

Reported: "for nanowells, there's basically no status update on the pie in the analyze plate map when running". The
dial follows two lines — a well's total and each piece of it done — which a field-by-field well printed as
``FOV-group`` / ``FOV k/n done`` and a nanowell well, processed a timepoint at a time, never printed at all. It now
prints steps (``pipeline.nanowell_task._log_steps``); ``test_nanowell_cli.py`` drives a real nanowell run through
the reader. This holds the reader on both spellings, as the run writes them.
"""

from __future__ import annotations

import pytest

from services.pipeline_runner import ProgressTracker


def _dial(lines):
    runner = ProgressTracker()
    return [payload for line in lines for kind, payload in runner.parse(line) if kind == "well_fov"]


def test_a_field_by_field_well_moves_its_dial_as_before():
    assert _dial(["12:00:01 INFO Well A01 — 3 FOV-group(s)", "12:00:09 INFO Well A01 — FOV 1/3 done",
                  "12:00:20 INFO Well A01 — FOV 3/3 done"]) == [("A01", 0, 3), ("A01", 1, 3), ("A01", 3, 3)]


def test_a_nanowell_well_moves_its_dial_by_steps():
    from pipeline.nanowell_task import _log_steps

    import logging

    seen = []

    class Collect(logging.Handler):
        def emit(self, record):
            seen.append(f"12:00:00 INFO {record.getMessage()}")

    handler = Collect()
    logger = logging.getLogger("allwell.nanowell")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        for done in range(5):
            _log_steps("B07", done, 4)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    assert _dial(seen) == [("B07", d, 4, "step") for d in range(5)]


# ── Inside a measure step: the nanowells segmented so far ────────────────────────────────────────────────────────────
# Asked: the dial moved in two steps a timepoint (located, then measured) — "why can't it tell me how many nanowells
# it's segmented out of the total". A measure step is most of a nanowell well's time (one Cellpose call per nanowell),
# so it now prints how many of the timepoint's nanowells are segmented, the reader turns that into ``well_sites``, and
# the dashboard fills the dial within the step and names the count in the well's hover text.


def test_the_reader_reads_a_segmented_count_and_never_takes_it_for_a_finished_well():
    from services import remote_wire
    from services.pipeline_runner import _RE_WELL_DONE_ID

    line = "12:00:00 INFO Well A01 — 24h: 120/400 nanowells segmented"
    assert [(k, p) for k, p in ProgressTracker().parse(line) if k == "well_sites"] == [
        ("well_sites", ("A01", "24h", 120, 400))]
    assert not _RE_WELL_DONE_ID.search(line) and not remote_wire._RE_WELL_DONE.search(line)
    assert _dial([line]) == []


def _counted(total, ticks):
    from pipeline.nanowell_run import _SiteCount

    said = []
    count = _SiteCount(lambda done, of: said.append((done, of)), total)
    for n in ticks:
        count.tick(n)
    count.close()
    return said


def test_a_timepoint_s_count_is_said_at_the_start_at_each_tenth_and_at_the_end():
    said = _counted(400, [1] * 400)
    assert said == [(d, 400) for d in range(0, 401, 40)]


def test_a_count_that_stops_short_says_where_it_stopped():
    """Three nanowells not segmented (too large for the template, say): the last line says 397, not 400."""
    said = _counted(400, [1] * 397)
    assert said[-1] == (397, 400) and said[-2] == (360, 400) and len(said) == 11


def test_a_whole_tile_segmented_at_once_counts_its_nanowells_at_once():
    assert _counted(100, [30, 30, 40]) == [(0, 100), (30, 100), (60, 100), (100, 100)]
    assert _counted(0, [1]) == []                                          # nothing imaged: nothing said


def test_the_crop_segmenter_reports_each_nanowell_it_segments():
    import numpy as np

    from pipeline import nanowell_measure as nm
    from pipeline.nanowell_detect import FITTED, SiteGeometry

    calls = []
    geoms = [SiteGeometry(i, (20.0 + 30 * i, 20.0), 10.0, FITTED, 0.2, 64) for i in range(3)]
    nm.segment_sites(np.zeros((40, 100)), 1.0, geoms, lambda crop: (calls.append(crop.shape), np.zeros_like(crop,
                     dtype=int))[1], on_site=lambda: calls.append("site"))
    assert calls[1::2] == ["site"] * 3 and len(calls) == 6                 # after each nanowell, not before


def test_the_dial_fills_within_a_step_and_its_hover_text_names_the_count(qapp):
    from widgets.run_dashboard import RunDashboard

    d = RunDashboard(); d.reset(remote=False, total=1)
    d.set_well_stage("A01", "active")
    d.set_well_fov("A01", 0, 4, unit="step")
    assert d._well_tooltip("A01") == "A01: processing — step 0/4"
    d.set_well_fov("A01", 1, 4, unit="step")                              # located; measuring the first timepoint
    d.set_well_sites("A01", "0h", 100, 400)
    assert d._plate._grid._fractions[(0, 0)] == pytest.approx((1 + 0.25) / 4)
    assert d._well_tooltip("A01") == "A01: processing — step 2/4, 0h: 100/400 nanowells segmented"
    d.set_well_fov("A01", 2, 4, unit="step")                              # that step done: the count is over
    assert d._plate._grid._fractions[(0, 0)] == pytest.approx(2 / 4)
    assert d._well_tooltip("A01") == "A01: processing — step 2/4"


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
