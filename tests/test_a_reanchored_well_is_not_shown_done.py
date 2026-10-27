"""A well the end-of-run re-anchoring re-runs is not shown finished while it runs.

Reported: on a remote batched nanowell run, a well was green ("downloaded") on the plate while the log said that
same well had segmented 796 of 1326 nanowells. Both were true. The well had finished, been fetched, and was then
re-run by ``pipeline.nanowell_task.reanchor_plate`` for the timepoints the plate's other wells could anchor, after
which the fetcher pulls it again (``services.remote_fetch_nanowells``). The plate never heard any of that, so a
well whose tables were being rewritten looked exactly like one that was done.

Driven through the real ``AnalyzeTab`` with the pipeline's and the fetcher's own log lines, read by the real
``ProgressTracker``; the assertions are on what the plate is told to paint (``setWellColor``), not on a value
computed behind it.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from ui import theme

WELL = "B03"


@pytest.fixture
def tab(qtbot):
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    painted: list[tuple[str, str | None]] = []
    plate = t._dashboard._plate
    real = plate.setWellColor
    plate.setWellColor = lambda w, c: (painted.append((w, c)), real(w, c))
    t.painted = painted
    yield t
    t._alive = False


def _feed(tab, *lines: str) -> None:
    from services.pipeline_runner import ProgressTracker

    reader = getattr(tab, "_test_reader", None) or ProgressTracker()
    tab._test_reader = reader
    for line in lines:
        for kind, payload in reader.parse(line):
            tab._apply_progress_event(kind, payload)


def _last_colour(tab) -> str | None:
    return [c for w, c in tab.painted if w == WELL][-1]


def _downloaded(tab, *, remote: bool = True) -> None:
    dash = tab._dashboard
    dash.reset(remote=remote, total=4)
    for stage in ("group", "upload", "process", "download") if remote else ("group", "process", "threshold"):
        dash.set_well_stage(WELL, stage)
    assert _last_colour(tab) == theme.Colors.success


REANCHOR = f"Well {WELL} — re-anchoring T03, T05 from the plate's other wells."
SITES = f"Well {WELL} — T03: 796/1326 nanowells segmented"


def test_a_downloaded_well_shows_computing_while_it_is_re_anchored(tab):
    _downloaded(tab)
    _feed(tab, REANCHOR, f"Well {WELL} — 4 step(s): its 2 timepoint(s), each located, then measured",
          f"Well {WELL} — step 2/4 done", SITES)
    assert _last_colour(tab) == theme.Colors.warn
    tip = tab._dashboard._well_tooltip(WELL)
    assert tip == (f"{WELL}: processing again — re-anchoring T03, T05 from the plate's other wells, "
                   "T03: 796/1326 nanowells segmented")
    fraction = tab._dashboard._well_fraction(WELL)
    assert fraction == pytest.approx((2 + 796 / 1326) / 4), "the dial follows the re-run, not the old full ring"


def test_it_waits_on_the_server_until_its_second_pull_lands(tab):
    _downloaded(tab)
    _feed(tab, REANCHOR, SITES, f"Well {WELL} — re-anchored. 8 timepoint(s), 0 without rows.")
    assert _last_colour(tab) == theme.Colors.accent, "re-anchored on the server, its new output not local yet"
    assert "downloaded again when the batch ends" in tab._dashboard._well_tooltip(WELL)
    _feed(tab, f"[remote] {WELL}: pulled again after the end-of-run re-anchoring re-ran it.\n")
    assert _last_colour(tab) == theme.Colors.success
    assert tab._dashboard._well_tooltip(WELL) == f"{WELL}: fetched locally"


def test_a_second_pull_that_falls_short_leaves_it_on_the_server(tab):
    _downloaded(tab)
    _feed(tab, REANCHOR, f"Well {WELL} — re-anchored. 8 timepoint(s), 0 without rows.",
          f"[remote] {WELL}: the re-anchored output did not all arrive; keeping it on the remote for "
          "verification to pull.\n")
    assert _last_colour(tab) == theme.Colors.accent


def test_on_a_local_run_it_is_complete_again_when_the_re_run_ends(tab):
    _downloaded(tab, remote=False)
    _feed(tab, REANCHOR, SITES)
    assert _last_colour(tab) == theme.Colors.warn
    _feed(tab, f"Well {WELL} — re-anchored. 8 timepoint(s), 0 without rows.")
    assert _last_colour(tab) == theme.Colors.success


def test_a_failed_re_anchoring_leaves_the_well_as_it_was_and_says_so(tab):
    _downloaded(tab)
    _feed(tab, REANCHOR, f"Well {WELL} — re-anchoring failed (ValueError: bad fit); its timepoints stay "
                         "unaddressed.")
    assert _last_colour(tab) == theme.Colors.success, "its data is the first run's, already local"
    assert "re-anchoring failed" in tab._dashboard._well_tooltip(WELL)


def test_a_new_run_forgets_the_last_one_s_re_anchoring(tab):
    _downloaded(tab)
    _feed(tab, REANCHOR)
    tab._dashboard.reset(remote=True, total=4)
    assert tab._dashboard._well_tooltip(WELL) == f"{WELL}: pending"
