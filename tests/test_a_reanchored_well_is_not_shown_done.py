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
    assert tab._dashboard._well_tooltip(WELL) == (
        f"{WELL}: on server — re-anchored on the server; its output is downloaded again when its batch or the "
        "run ends")
    _feed(tab, f"[remote] {WELL}: pulled again after the end-of-run re-anchoring re-ran it.\n")
    assert _last_colour(tab) == theme.Colors.success
    assert tab._dashboard._well_tooltip(WELL) == f"{WELL}: fetched locally"


@pytest.mark.parametrize("line", [
    # release_batch's, at the end of a batch, and pull_nanowell_results', at verification
    f"[remote] {WELL}: the re-anchored output did not all arrive; keeping it on the remote for verification to "
    "pull.\n",
    f"[remote] {WELL}: the re-anchored output did not all arrive; keeping it on the remote.\n",
])
def test_a_second_pull_that_falls_short_leaves_it_on_the_server_and_says_why(tab, line):
    _downloaded(tab)
    _feed(tab, REANCHOR, f"Well {WELL} — re-anchored. 8 timepoint(s), 0 without rows.", line)
    assert _last_colour(tab) == theme.Colors.accent
    assert tab._dashboard._well_tooltip(WELL) == (
        f"{WELL}: on server — its re-anchored output did not all arrive; the copy here predates the "
        "re-anchoring, and the remote keeps the new one")


def test_a_re_run_that_placed_nothing_leaves_the_well_as_it_was(tab):
    """No timepoint placed means no second pull: the well must not wait for one (review D1). The line is the
    pipeline's own, from the helper ``run_zip_task`` logs with."""
    from pipeline.nanowell_task import reanchored_line

    grid = {"timepoints": {"T03": {"status": "unaddressed", "origin_method": None}}}
    _downloaded(tab)
    _feed(tab, REANCHOR, reanchored_line(WELL, grid, {"T03": object()}, 8, 1))
    assert _last_colour(tab) == theme.Colors.success
    assert tab._dashboard._well_tooltip(WELL) == (
        f"{WELL}: fetched locally — re-anchoring placed none of its unaddressed timepoints; the result here "
        "stands")


def test_the_pipeline_says_re_anchored_only_when_a_timepoint_was_placed():
    from pipeline.nanowell_task import reanchored_line

    placed = {"timepoints": {"T03": {"origin_method": "plate_rigid"}, "T05": {"origin_method": None}}}
    none = {"timepoints": {"T03": {"origin_method": "fiducial"}, "T05": {"origin_method": None}}}
    asked = {"T03": object(), "T05": object()}
    assert reanchored_line(WELL, placed, asked, 8, 0) == f"Well {WELL} — re-anchored. 8 timepoint(s), 0 without rows."
    assert reanchored_line(WELL, none, asked, 8, 0) == (
        f"Well {WELL} — re-anchoring placed none of T03, T05; its earlier result stands.")


def test_verification_says_a_short_second_pull_and_keeps_the_remote(tmp_path):
    """Review D2: it printed "pulled again" whatever rsync did, and the plate went green over the old copy."""
    from types import SimpleNamespace

    from services.remote_fetch_nanowells import pull_nanowell_results

    folder = tmp_path / "nanowells"
    folder.mkdir()
    for well in ("A01", "B02"):
        (folder / f"{well}_grid.json").write_text(
            '{"well": "%s", "timepoints": {"T03": {"origin_method": "plate_rigid"}}}' % well)
    lines: list[str] = []
    verifier = SimpleNamespace(
        _run=SimpleNamespace(local_output_dir=tmp_path, remote_output_dir="/r/out", nanowell=True,
                             nanowell_released=set(), fetched_wells={"A01", "B02"}),
        host=SimpleNamespace(ssh_alias="gpu"),
        # B02's CSV does not arrive; everything else does.
        _rsync_optional=lambda src, dest, *, log_fn, absent_note="": not src.endswith("_B02.csv.gz"))
    short = pull_nanowell_results(verifier, log=lines.append)
    assert short == ["the re-anchored output of B02 (the copy here predates the re-anchoring)"]
    assert "[remote] A01: pulled again after the end-of-run re-anchoring re-ran it.\n" in lines
    assert "[remote] B02: the re-anchored output did not all arrive; keeping it on the remote.\n" in lines
    assert not any("B02: pulled again" in line for line in lines)


def test_a_download_after_the_re_run_brings_its_output(tab):
    """Review D3: the end-of-run retry of a well whose first download failed fetches the re-anchored output."""
    dash = tab._dashboard
    dash.reset(remote=True, total=4)
    for stage in ("group", "upload", "process"):
        dash.set_well_stage(WELL, stage)
    _feed(tab, REANCHOR, f"Well {WELL} — re-anchored. 8 timepoint(s), 0 without rows.")
    assert _last_colour(tab) == theme.Colors.accent
    dash.set_well_stage(WELL, "download")
    assert _last_colour(tab) == theme.Colors.success


def test_the_rail_does_not_count_a_well_being_re_anchored(tab):
    """Review D5: "Download 1/1" over a plate showing the well amber."""
    _downloaded(tab)
    dash = tab._dashboard
    assert dash.stage_progress("download")[0] == 1
    _feed(tab, REANCHOR)
    assert (dash.stage_progress("process")[0], dash.stage_progress("download")[0]) == (0, 0)
    _feed(tab, f"Well {WELL} — re-anchored. 8 timepoint(s), 0 without rows.")
    assert (dash.stage_progress("process")[0], dash.stage_progress("download")[0]) == (1, 0)
    _feed(tab, f"[remote] {WELL}: pulled again after the end-of-run re-anchoring re-ran it.\n")
    assert dash.stage_progress("download")[0] == 1


def test_a_run_that_ends_during_the_re_anchoring_says_it_did_not_finish(tab):
    """Review D6: a Stop left it amber "processing again" on a run that was over."""
    _downloaded(tab)
    _feed(tab, REANCHOR, SITES)
    tab._dashboard.set_cancelled("stopped")
    assert _last_colour(tab) == theme.Colors.success
    assert tab._dashboard._well_tooltip(WELL) == (
        f"{WELL}: fetched locally — the run ended before its re-anchoring did; its unaddressed timepoints stay "
        "unaddressed")


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


def test_verification_keeps_the_remote_copy_of_a_short_second_pull(tmp_path, monkeypatch):
    """The other half of D2: the remote holds the only re-anchored copy, so cleanup must not remove it."""
    import threading
    from types import SimpleNamespace

    from services import remote_fetch

    run = SimpleNamespace(local_output_dir=tmp_path, remote_output_dir="/r/out", local_input_dir=tmp_path,
                          incremental=False, nanowell=True, expected_wells=set(), force_wells=frozenset())
    verifier = remote_fetch.FetchedOutputVerifier(SimpleNamespace(ssh_alias="gpu"), run, None, threading.Event())
    monkeypatch.setattr(verifier, "_rsync_optional", lambda *a, **k: True)
    monkeypatch.setattr(verifier, "_verify_local_manifest", lambda *, log: [])
    monkeypatch.setattr(verifier, "_reconcile_fetch", lambda *, log: "")
    monkeypatch.setattr(remote_fetch, "pull_nanowell_results", lambda v, *, log: ["the re-anchored output of B02"])
    monkeypatch.setattr(remote_fetch, "refresh_pipeline_info_available_from_outputs", lambda *a, **k: None)
    monkeypatch.setattr(remote_fetch, "run_client_side_completeness_check", lambda *a, **k: None)
    result = verifier.verify(True, log=lambda _m: None)
    assert (result.passed, result.reason) == (False, "incomplete")
    assert result.missing == ("the re-anchored output of B02",)
