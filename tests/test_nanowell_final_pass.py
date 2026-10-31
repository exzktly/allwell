"""A batched nanowell run's re-anchoring is one pass after the last batch, its wells in parallel.

It used to run at the end of every batch, in the pipeline's own process after the wells' pool had closed — one well
after another while the node's other cores waited — and from only that batch's wells, whose archives the next
batch then deleted. A well its batch could not anchor stayed unaddressed even when later batches would have
supplied the anchors. Now each batch holds such wells' archives (``--nanowell_hold_dir``), and after the last data
batch the driver runs one more, ``--nanowell_reanchor_only``, over them, fitted from the whole plate.
"""

from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import pytest

from pipeline import nanowell_task as nt
from services.remote_final_pass import run_final_pass


def _grid(well, timepoints):
    return {"well": well, "scheme_version": nt.SCHEME_VERSION, "timepoints": timepoints}


ANCHORED = {"status": "ok", "origin_method": "fiducial", "origin_stage_um": [1.0, 2.0]}
WAITING = {"status": "unaddressed", "stage_offset_um": [0.0, 0.0]}


# ── which wells wait for the final pass (F7) ─────────────────────────────────

def test_a_well_waits_when_an_unaddressed_timepoint_could_be_anchored():
    assert nt.awaits_final_pass(_grid("B03", {"T01": ANCHORED, "T05": WAITING, "T03": WAITING})) == ["T03", "T05"]


def test_a_well_never_anchored_on_its_own_does_not_wait():
    """The plate's motion carries the well's own origin from another timepoint; without one there is nothing to
    carry, so holding its archive would cost remote disk for nothing."""
    assert nt.awaits_final_pass(_grid("B03", {"T01": WAITING, "T05": WAITING})) == []


def test_a_timepoint_without_stage_positions_does_not_wait():
    no_stage = {"status": "unaddressed", "stage_offset_um": None}
    assert nt.awaits_final_pass(_grid("B03", {"T01": ANCHORED, "T05": no_stage})) == []


def test_a_well_anchored_throughout_does_not_wait():
    assert nt.awaits_final_pass(_grid("B03", {"T01": ANCHORED, "T05": ANCHORED})) == []


# ── a batch holds instead of re-anchoring (F2) ───────────────────────────────

KWARGS = {"_nanowell": {"plate_format": "100um"}}


def _plate(tmp_path, grids, kwargs=KWARGS):
    out, src = tmp_path / "out", tmp_path / "in"
    here = nt.settings_from_kwargs(kwargs).key()
    (out / nt.NANOWELL_DIR).mkdir(parents=True)
    src.mkdir()
    zips = []
    for well, timepoints in grids.items():
        (out / nt.NANOWELL_DIR / f"{well}_grid.json").write_text(json.dumps({**_grid(well, timepoints),
                                                                               "settings": here}))
        archive = src / f"{well}.zip"
        archive.write_bytes(b"zip of " + well.encode())
        zips.append((well, archive))
    return out, zips


def test_a_batch_holds_the_wells_the_final_pass_can_anchor_and_re_anchors_nothing(tmp_path, monkeypatch, caplog):
    import logging

    out, zips = _plate(tmp_path, {"A01": {"T01": ANCHORED}, "B03": {"T01": ANCHORED, "T05": WAITING}})
    monkeypatch.setattr(nt, "reanchor_plate", lambda *a, **k: pytest.fail("a held batch re-anchored"))
    keep = tmp_path / "keep"
    with caplog.at_level(logging.INFO, logger="allwell.nanowell"):
        nt.nanowell_endgame({"plate_format": "100um"}, out, zips, {"_nanowell": {"plate_format": "100um"}}, "PHASE",
                            [], "m", ["experiment"], "_", "key", grouper=None, parse=None, hold_dir=keep)
    assert sorted(p.name for p in keep.iterdir()) == ["B03.zip"]
    assert (keep / "B03.zip").read_bytes() == b"zip of B03"
    assert os.stat(keep / "B03.zip").st_ino == os.stat(zips[1][1]).st_ino, "a hard link, no bytes copied"
    assert "Well B03 — held for the end-of-run re-anchoring (T05 unaddressed)." in caplog.messages
    assert not (out / "pipeline_info.json").exists(), "the plate's re-anchoring record is the final pass's to write"


# ── the pass runs its wells in parallel (F1) ─────────────────────────────────

def _fake_rerun(calls, job):
    well, _zip, by_tp = job
    calls.append((well, threading.current_thread().name))
    return well, by_tp, "" if well != "C05" else "ValueError: no fit"


def test_the_pass_hands_each_well_to_the_pool(tmp_path):
    calls: list = []
    jobs = [(w, tmp_path / f"{w}.zip", {"T05": object()}) for w in ("A01", "B03", "C05")]
    results = nt._each_reanchored(partial(_fake_rerun, calls), jobs,
                                  lambda: ThreadPoolExecutor(max_workers=3, thread_name_prefix="pool"))
    assert [r[0] for r in results] == ["A01", "B03", "C05"]
    assert all(name.startswith("pool") for _w, name in calls), "a well re-ran outside the pool"
    assert results[2][2] == "ValueError: no fit"


def test_a_worker_that_dies_fails_its_own_well_only(tmp_path):
    from concurrent.futures.process import BrokenProcessPool

    def rerun(job):
        if job[0] == "B03":
            raise BrokenProcessPool("a worker died")
        return job[0], job[2], ""

    jobs = [(w, tmp_path / f"{w}.zip", {"T05": 1}) for w in ("A01", "B03", "C05")]
    results = nt._each_reanchored(rerun, jobs, lambda: ThreadPoolExecutor(max_workers=3))
    assert [(w, e) for w, _tp, e in results] == [("A01", ""), ("B03", "BrokenProcessPool: a worker died"),
                                                   ("C05", "")]


def test_without_a_pool_the_wells_run_here(tmp_path):
    calls: list = []
    jobs = [(w, tmp_path / f"{w}.zip", {}) for w in ("A01", "B03")]
    nt._each_reanchored(partial(_fake_rerun, calls), jobs, None)
    assert {name for _w, name in calls} == {threading.current_thread().name}


def test_a_well_s_re_run_can_be_sent_to_a_worker_process():
    """Spawned workers receive the job by pickle: the re-run and everything bound into it must pickle."""
    from pipeline.io import write_well_csv

    rerun = partial(nt._reanchor_well, output_dir=Path("/o"), shared_kwargs_template={}, nuclear_token="P",
                    fluor_tokens=[], csv_prefix="m", schema=None, sep="_", settings_key="k",
                    grouper=write_well_csv, parse=write_well_csv, segmenter=None)
    assert pickle.loads(pickle.dumps(rerun)).func is nt._reanchor_well


def test_reanchor_plate_runs_its_wells_through_the_pool(tmp_path, monkeypatch):
    """Through ``reanchor_plate`` itself: the pool it is given is the one the wells run on."""
    out, zips = _plate(tmp_path, {"B03": {"T01": ANCHORED, "T05": WAITING}, "C05": {"T01": ANCHORED, "T05": WAITING}})
    monkeypatch.setattr(nt, "plan_reanchor", lambda members: ({"B03": {"T05": 1}, "C05": {"T05": 1}}, {}))
    ran: list = []
    monkeypatch.setattr(nt, "run_zip_task", lambda well, *a, **k: ran.append((well, threading.current_thread().name)))
    used: list = []

    def pool():
        used.append(True)
        return ThreadPoolExecutor(max_workers=2, thread_name_prefix="reanchor")

    nt.reanchor_plate(out, zips, KWARGS, "P", [], "m", ["experiment"], "_", "k",
                      grouper=None, parse=None, pool=pool)
    assert used == [True]
    assert sorted(w for w, _t in ran) == ["B03", "C05"]
    assert all(name.startswith("reanchor") for _w, name in ran)


def test_the_empty_reference_well_is_re_anchored_before_the_wells_read_against_it(tmp_path, monkeypatch):
    """Review tracker L8: every well's confluence is read against the empty reference well's saved templates, and
    the re-anchoring pass ran the reference in the same pool as the wells that read it, so a well could read the
    reference while it was being rewritten — the old one or a half-saved one. The reference now runs first, alone,
    and only then the rest, in the pool."""
    kwargs = {"_nanowell": {"plate_format": "100um", "empty_well": "C05"}}
    wells = {w: {"T01": ANCHORED, "T05": WAITING} for w in ("B03", "C05", "D07")}
    out, zips = _plate(tmp_path, wells, kwargs)
    monkeypatch.setattr(nt, "plan_reanchor", lambda members: ({w: {"T05": 1} for w in wells}, {}))
    events: list = []
    lock = threading.Lock()

    def rerun(well, *a, **k):
        with lock:
            events.append(("start", well))
        if well == "C05":
            threading.Event().wait(0.3)          # long enough for a pool to have started the others beside it
        with lock:
            events.append(("end", well))

    monkeypatch.setattr(nt, "run_zip_task", rerun)
    nt.reanchor_plate(out, zips, kwargs, "P", [], "m", ["experiment"], "_", "k", grouper=None, parse=None,
                      pool=lambda: ThreadPoolExecutor(max_workers=3, thread_name_prefix="reanchor"))
    assert events[:2] == [("start", "C05"), ("end", "C05")]
    assert sorted(w for kind, w in events[2:] if kind == "start") == ["B03", "D07"]


# ── the driver runs the final pass (F4) ──────────────────────────────────────

def _drive(tmp_path, flag):
    from services.remote_commands import BATCH_READY, _batch_driver_snippet

    for i, well in ((1, "A01"), (2, "B02"), (3, "B03")):
        (tmp_path / f"b{i}").mkdir()
        (tmp_path / f"b{i}" / f"{well}.zip").write_text("x")
        (tmp_path / BATCH_READY.format(i=i)).touch()
    (tmp_path / flag.format(i=3)).touch()
    argv = [sys.executable, "-c", "import sys, os; print('RAN', sys.argv[3:], sorted(os.listdir(sys.argv[2])))",
            "--input_dir", str(tmp_path / "in")]
    out = subprocess.run(["bash", "-c", _batch_driver_snippet(str(tmp_path), 2, argv, final_pass=True)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return [line for line in out.stdout.splitlines() if line.startswith("RAN")], out.stdout


def test_the_driver_runs_the_held_wells_with_the_re_anchoring_alone(tmp_path):
    from services.remote_commands import BATCH_REANCHOR

    ran, stdout = _drive(tmp_path, BATCH_REANCHOR)
    assert ran == ["RAN [] ['A01.zip']", "RAN [] ['B02.zip']", "RAN ['--nanowell_reanchor_only'] ['B03.zip']"]
    assert "batch 3 finished rc=0" in stdout and "all 3 batch(es) done" in stdout


def test_with_nothing_held_the_driver_runs_no_third_pipeline(tmp_path):
    from services.remote_commands import BATCH_SKIP

    ran, stdout = _drive(tmp_path, BATCH_SKIP)
    assert ran == ["RAN [] ['A01.zip']", "RAN [] ['B02.zip']"]
    assert "batch 3 finished rc=0" in stdout


def test_a_final_batch_with_no_flag_runs_no_pipeline(tmp_path):
    """A `touch` of the re-anchoring flag that failed must not run the whole pipeline over the held wells."""
    ran, stdout = _drive(tmp_path, ".aw_batch_{i}_unrelated")
    assert ran == ["RAN [] ['A01.zip']", "RAN [] ['B02.zip']"]
    assert "batch 3 finished rc=0" in stdout


def test_a_run_that_is_not_nanowell_keeps_its_batches_alone():
    from services.remote_commands import _batch_driver_snippet

    script = _batch_driver_snippet("/r/run-x", 2, ["python", "pm.py"])
    assert "--nanowell_reanchor_only" not in script and '-le 2' in script


# ── the app stages it, runs it and releases what it held (F5, F6) ────────────

def test_a_data_batch_keeps_the_wells_it_held(tmp_path, monkeypatch):
    from services import remote_fetch_nanowells as rfn

    deleted: list = []
    monkeypatch.setattr("services.remote_transport.delete_remote_paths",
                        lambda host, paths, **k: deleted.append(list(paths)))
    fetcher = types.SimpleNamespace(
        run=types.SimpleNamespace(nanowell=True, fetched_wells={"A01", "B03"}, nanowell_released=set(),
                                  local_output_dir=tmp_path, remote_output_dir="/r/out", remote_input_dir="/r/in"),
        host=types.SimpleNamespace(ssh_alias="gpu"),
        _rsync_glob=lambda *a: (0, 0))
    monkeypatch.setattr(rfn, "fetch_nanowell_tables", lambda f, w: 0)        # pulled, nothing new: None would mean the pull failed
    lines: list = []
    released = rfn.release_batch(fetcher, ["A01", "B03"], log=lines.append, held={"B03"})
    assert released == ["A01"]
    assert all("B03" not in p for paths in deleted for p in paths), "a held well's files were deleted"
    assert "[remote] B03: kept on the remote for the end-of-run re-anchoring.\n" in lines
    assert rfn.release_batch(fetcher, ["B03"], log=lines.append) == ["B03"], "the final pass's release frees it"


def _listing(stdout, rc=0):
    return lambda argv, **k: types.SimpleNamespace(stdout=stdout, returncode=rc)


def test_the_held_wells_are_listed_by_their_well_name():
    from services.remote_fetch_nanowells import held_wells

    assert held_wells(_listing("/r/keep/b3.zip\n/r/keep/C05.zip\n"), "/r/keep") == {"B03", "C05"}
    assert held_wells(_listing(""), "/r/keep") == set()


def test_a_listing_that_fails_is_not_an_empty_one():
    """Read as "nothing held", a failed listing would release — delete — the archive and output the pass needs."""
    from services.remote_fetch_nanowells import held_wells

    assert held_wells(_listing("", rc=124), "/r/keep") is None
    assert held_wells(lambda argv, **k: None, "/r/keep") is None


def _releasing(tmp_path, listing):
    from tests.test_batch_driver import _batched

    p = _batched(tmp_path, ["A01", "B03"])
    p._runner = types.SimpleNamespace(_cache=types.SimpleNamespace(_ssh_run=listing),
                                      host=types.SimpleNamespace(remote_staging_dir="/r"))
    queued: list = []
    p._fetcher = types.SimpleNamespace(run=types.SimpleNamespace(nanowell=True), after_queued=queued.append)
    return p, queued


def test_a_batch_s_held_wells_are_read_when_it_ends_not_when_its_release_runs(tmp_path):
    """The release runs later, behind the batch's downloads — by then the final pass may have moved ``keep/`` away,
    and a listing taken then finds nothing held and deletes the pass's own input."""
    state = {"listing": "/r/runs/run-test/keep/B03.zip\n"}
    p, queued = _releasing(tmp_path, lambda argv, **k: types.SimpleNamespace(stdout=state["listing"], returncode=0))
    p._release_nanowell_batch(0, [tmp_path / "A01.zip", tmp_path / "B03.zip"])
    state["listing"] = ""                                    # `keep/` moved into the final batch
    assert queued[0].keywords["held"] == {"B03"}


def test_a_batch_whose_held_wells_cannot_be_listed_keeps_them_all(tmp_path):
    p, queued = _releasing(tmp_path, _listing("", rc=255))
    p._release_nanowell_batch(0, [tmp_path / "A01.zip", tmp_path / "B03.zip"])
    assert set(queued[0].keywords["held"]) == {"A01", "B03"}
    assert "could not list the wells held" in "".join(p._logged)


def _final_pass_process(tmp_path, listing, *, mv_rc=0, upload_rc=0, pass_rc=0):
    from tests.test_batch_driver import _batched

    p = _batched(tmp_path, ["A01", "B03", "C05"])
    asked: list = []

    def ssh_run(argv, *, check, **kw):
        asked.append(list(argv))
        if argv[0] == "find":
            return types.SimpleNamespace(stdout=listing, returncode=0)
        if argv[0] == "mv":
            return types.SimpleNamespace(stdout="", returncode=mv_rc)
        return types.SimpleNamespace(stdout="", returncode=0)

    uploaded: list = []

    def upload_subset(zips, dest, **kw):
        uploaded.append(([z.stem for z in zips], dest, kw.get("acquisition_input")))
        return 0, 0.0, upload_rc

    def ssh_script(script, *, check):
        asked.append(["script", script])
        return types.SimpleNamespace(stdout="", returncode=0)

    p._runner = types.SimpleNamespace(_cache=types.SimpleNamespace(_ssh_run=ssh_run, _ssh_script=ssh_script,
                                                                   upload_subset=upload_subset),
                                      host=types.SimpleNamespace(remote_staging_dir="/r"))
    p._uploaded = uploaded
    p._await_batch = lambda index, n: pass_rc
    released: list = []
    p._release_nanowell_batch = lambda rc, zips, final=False: released.append(([z.stem for z in zips], final))
    return p, asked, released


def test_the_final_pass_takes_the_held_wells_and_releases_them(tmp_path, monkeypatch):
    for module in ("services.remote_final_pass", "services.remote_process"):
        monkeypatch.setattr(f"{module}.remote_run_dir", lambda host, run_id: "/r/run")
    p, asked, released = _final_pass_process(tmp_path, "/r/run/keep/B03.zip\n/r/run/keep/C05.zip\n")
    assert run_final_pass(p, 2) == 0
    assert ["mv", "/r/run/keep", "/r/run/b3"] in asked
    touched = [a[1] for a in asked if a[0] == "touch"]
    assert touched == ["/r/run/.aw_batch_3_reanchor", "/r/run/.aw_batch_3_ready"]
    assert released == [(["B03", "C05"], True)]
    assert "end-of-run re-anchoring: 2 held well(s) — B03, C05" in "".join(p._logged)
    assert p._uploaded == [(["B03", "C05"], "/r/run/b3", p._zipped)], (
        "the pipeline reads the acquisition record and anchors from the archives' folder; keep/ has neither")
    assert asked.index(["mv", "/r/run/keep", "/r/run/b3"]) < [a[0] for a in asked].index("touch")


def test_a_record_that_cannot_be_sent_runs_no_pass(tmp_path, monkeypatch):
    for module in ("services.remote_final_pass", "services.remote_process"):
        monkeypatch.setattr(f"{module}.remote_run_dir", lambda host, run_id: "/r/run")
    p, asked, released = _final_pass_process(tmp_path, "/r/run/keep/B03.zip\n", upload_rc=23)
    run_final_pass(p, 2)
    assert [a[1] for a in asked if a[0] == "touch"][0] == "/r/run/.aw_batch_3_skip"
    assert "could not send the acquisition record" in "".join(p._logged)


def test_a_pass_that_fails_keeps_its_wells_on_the_remote(tmp_path, monkeypatch):
    for module in ("services.remote_final_pass", "services.remote_process"):
        monkeypatch.setattr(f"{module}.remote_run_dir", lambda host, run_id: "/r/run")
    p, asked, released = _final_pass_process(tmp_path, "/r/run/keep/B03.zip\n", pass_rc=1)
    assert run_final_pass(p, 2) == 1
    assert released == [], "a failed pass's wells were released (deleted from the remote)"
    # Tracker F4: and their archives go back to keep/, where a later run's pass looks (driven for real below).
    assert any(a[0] == "script" and "/r/run/b3" in a[1] and "/r/run/keep" in a[1] for a in asked), asked


def test_the_staged_record_is_the_one_the_pipeline_reads(tmp_path, monkeypatch):
    """Through the real upload: the acquisition record and the anchors travel beside the held archive."""
    import services.remote_upload as ru
    from services.acquisition_transport import ANCHORS_FILENAME, RECORD_FILENAME

    sent: list = []
    monkeypatch.setattr(ru, "_stream_rsync_with_retry",
                        lambda cmd, **k: sent.append([Path(c).name for c in cmd]) or (0, 0.0, 0, 1))
    monkeypatch.setattr(ru, "resolve_rsync_binary", lambda: "rsync")
    monkeypatch.setattr(ru, "_rsync_ssh_transport", lambda host: [])
    monkeypatch.setattr(ru, "_rsync_progress_flags", lambda: [])
    archive = tmp_path / "B03.zip"
    archive.write_bytes(b"x")
    zipped = types.SimpleNamespace(acquisition_record={"wells": {}}, acquisition_schema=None, acquisition_sep="_",
                                   nanowell_anchors=b"{}")
    monkeypatch.setattr("services.acquisition_transport.filter_record_to_paths", lambda paths, record, **k: record)
    monkeypatch.setattr("services.acquisition_transport.write_record_file", lambda path, record: path.write_text("{}"))
    cache = object.__new__(ru.DatasetCache)
    cache.host = types.SimpleNamespace(ssh_alias="gpu")
    cache.upload_subset([archive], "/r/run/b3", acquisition_input=zipped)
    assert {"B03.zip", RECORD_FILENAME, ANCHORS_FILENAME} <= set(sent[0])


def test_with_nothing_held_the_final_pass_is_skipped(tmp_path, monkeypatch):
    for module in ("services.remote_final_pass", "services.remote_process"):
        monkeypatch.setattr(f"{module}.remote_run_dir", lambda host, run_id: "/r/run")
    p, asked, released = _final_pass_process(tmp_path, "")
    run_final_pass(p, 2)
    assert [a[1] for a in asked if a[0] == "touch"] == ["/r/run/.aw_batch_3_skip", "/r/run/.aw_batch_3_ready"]
    assert not any(a[0] == "mv" for a in asked)
    assert released == []


def test_held_wells_that_cannot_be_listed_are_not_read_as_none_held(tmp_path, monkeypatch):
    """A failed listing of keep/ said "no well was held for it" and the run reported success over held wells whose
    pass never ran — indistinguishable from a pass that did."""
    for module in ("services.remote_final_pass", "services.remote_process"):
        monkeypatch.setattr(f"{module}.remote_run_dir", lambda host, run_id: "/r/run")
    p, asked, released = _final_pass_process(tmp_path, "/r/run/keep/B03.zip\n")
    real = p._runner._cache._ssh_run

    def listing_fails(argv, **kw):
        return types.SimpleNamespace(stdout="", returncode=255) if argv[0] == "find" else real(argv, **kw)

    p._runner._cache._ssh_run = listing_fails
    assert run_final_pass(p, 2) == 1, "a pass that never ran over its held wells was reported as a success"
    log = "".join(p._logged)
    assert "no well was held" not in log
    assert "ERROR could not list the wells held in /r/run/keep" in log
    assert not any(a[0] == "mv" for a in asked)
    assert released == []


def test_a_pass_directory_that_cannot_be_cleared_is_not_staged_into(tmp_path, monkeypatch):
    """`mv keep b3` onto a b3 that survived its `rm` moves keep/ inside it, where the pass does not look."""
    for module in ("services.remote_final_pass", "services.remote_process"):
        monkeypatch.setattr(f"{module}.remote_run_dir", lambda host, run_id: "/r/run")
    p, asked, released = _final_pass_process(tmp_path, "/r/run/keep/B03.zip\n")
    real = p._runner._cache._ssh_run

    def rm_fails(argv, **kw):
        return types.SimpleNamespace(stdout="", stderr="rm: Permission denied", returncode=1) if argv[0] == "rm" \
            else real(argv, **kw)

    p._runner._cache._ssh_run = rm_fails
    assert run_final_pass(p, 2) == 1
    assert not any(a[0] == "mv" for a in asked)
    assert "ERROR could not clear /r/run/b3 for the end-of-run re-anchoring (ssh exit 1: rm: Permission denied)" in \
        "".join(p._logged)


def test_a_staging_failure_says_the_held_wells_stay_unaddressed(tmp_path, monkeypatch):
    for module in ("services.remote_final_pass", "services.remote_process"):
        monkeypatch.setattr(f"{module}.remote_run_dir", lambda host, run_id: "/r/run")
    p, asked, released = _final_pass_process(tmp_path, "/r/run/keep/B03.zip\n", mv_rc=1)
    assert run_final_pass(p, 2) == 1, "held wells whose pass never ran were reported as a success"
    assert [a[1] for a in asked if a[0] == "touch"][0] == "/r/run/.aw_batch_3_skip"
    assert "their unaddressed timepoints stay unaddressed" in "".join(p._logged)
    assert released == [], "nothing to release: the held wells were never re-run"


# ── the plate says which wells wait, and why one was not placed ──────────────

def test_a_held_well_says_so_and_the_pass_s_reasons_reach_its_hover_text(qtbot):
    from services.pipeline_runner import ProgressTracker
    from widgets.run_dashboard import RunDashboard

    dash = RunDashboard()
    qtbot.addWidget(dash)
    dash.reset(remote=True, total=2)
    for stage in ("group", "upload", "process", "download"):
        dash.set_well_stage("B03", stage)
    reader = ProgressTracker()
    for line in ("Well B03 — held for the end-of-run re-anchoring (T05 unaddressed).",
                 "Unaddressed B03/T05: the plate's motion could not be fitted from 2 other well(s)"):
        for kind, payload in reader.parse(line):
            dash.set_well_reanchor(*payload)
        if line.startswith("Well"):
            assert dash._well_tooltip("B03") == ("B03: fetched locally — held for the end-of-run re-anchoring: T05 "
                                                 "re-run once the last batch is done")
    assert dash._well_tooltip("B03") == ("B03: fetched locally — re-anchoring could not place T05: the plate's "
                                         "motion could not be fitted from 2 other well(s)")
    dash.set_done("Complete")
    assert "could not place T05" in dash._well_tooltip("B03"), "a well the pass considered did not 'miss' it"


def test_a_run_that_ends_before_the_final_pass_says_its_held_wells_were_not_re_anchored(qtbot):
    from widgets.run_dashboard import RunDashboard

    dash = RunDashboard()
    qtbot.addWidget(dash)
    dash.reset(remote=True, total=1)
    dash.set_well_reanchor("B03", "held", "T05")
    dash.set_cancelled("suspended; resume to finish")
    assert dash._well_tooltip("B03").endswith(
        "the run ended before its re-anchoring did; its unaddressed timepoints stay unaddressed")


# ── the pipeline's two batch modes, through main (F2, F3) ───────────────────

def _main(tmp_path, monkeypatch, *extra):
    import zipfile

    import numpy as np
    from tifffile import imwrite

    import pipeline.suspend as _suspend
    import process_microscopy as pm

    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    tif = tmp_path / "x.tif"
    imwrite(tif, np.zeros((8, 8), np.uint16))
    with zipfile.ZipFile(plate / "in" / "B03.zip", "w") as archive:
        archive.write(tif, "exp_PHASE_B03_f0_0h.tif")
        archive.write(tif, "exp_GFP_B03_f0_0h.tif")
    calls: list = []
    monkeypatch.setattr(pm, "process_well_zips", lambda **k: calls.append(("wells", k["reanchor_only"])) or [])
    monkeypatch.setattr(pm, "nanowell_endgame",
                        lambda *a, **k: calls.append(("pass", k.get("hold_dir"), callable(k.get("pool")))))
    monkeypatch.setattr(pm, "finalize_run_report", lambda *a, **k: calls.append(("report", list(k["ran_wells"]))))
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(plate / "out"),
        "--phase_token", "PHASE", "--fluor_tokens", "GFP", "--filename_schema", "experiment:channel:well:fov:timepoint",
        "--workers", "1", "--background", "none", "--flatfield", "none", "--no_auto_threshold", "--no_run_check",
        "--nanowell_format", "100um", "--nanowell_hold_dir", str(tmp_path / "keep"), *extra])
    pm.main()
    return calls


def test_a_data_batch_processes_its_wells_then_holds(tmp_path, monkeypatch):
    calls = _main(tmp_path, monkeypatch)
    assert calls == [("wells", False), ("pass", str(tmp_path / "keep"), True), ("report", ["B03"])]


def test_the_final_pass_processes_no_well_and_keeps_every_earlier_record(tmp_path, monkeypatch):
    """Nothing is processed, the pass runs here (not held again, though the argv still names the hold folder), on
    the pool, and no well's earlier coverage record is dropped: a held well the pass cannot place keeps the one its
    batch wrote, and a re-run well's new record replaces it."""
    calls = _main(tmp_path, monkeypatch, "--nanowell_reanchor_only")
    assert calls == [("wells", True), ("pass", None, True), ("report", [])]


def test_process_well_zips_processes_none_in_the_final_pass(tmp_path, monkeypatch):
    import process_microscopy as pm

    monkeypatch.setattr(pm, "_well_pool", lambda *a, **k: pytest.fail("a pool was started for the final pass"))
    assert pm.process_well_zips(input_dir=tmp_path, output_dir=tmp_path / "out",
                                well_zips=[("B03", tmp_path / "B03.zip")], shared_kwargs_template={},
                                nuclear_token="P", fluor_tokens=[], workers=2, csv_prefix="m",
                                reanchor_only=True) == []


# ── the batch loop runs the final pass once, after every data batch (F5, F8) ─

def _nanowell_batched(tmp_path, monkeypatch, ordering):
    from tests.test_batch_driver import _batched, _FakeProc, _stub_runner

    proc = _FakeProc()
    p = _batched(tmp_path, ["A01", "A02"])
    _stub_runner(p, proc, ordering)
    p._fetcher.run.nanowell = True
    p._fetcher.after_queued = lambda fn: ordering.append(f"release:{','.join(fn.keywords['wells'])}")
    import services.remote_process as rp

    built: list = []
    real = rp._batch_driver_snippet
    monkeypatch.setattr(rp, "_batch_driver_snippet",
                        lambda run_dir, n, argv, *a, **k: built.append((n, list(argv), k.get("final_pass")))
                        or real(run_dir, n, argv, *a, **k))
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: proc)
    return p, proc, built


def test_the_loop_runs_the_final_pass_after_the_last_data_batch(tmp_path, monkeypatch):
    ordering: list = []
    p, proc, built = _nanowell_batched(tmp_path, monkeypatch, ordering)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    for i in (1, 2, 3):
        proc.batch_finished(p, i)
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive()
    assert p._returncode == 0
    [(n_data, argv, final_pass)] = built
    assert (n_data, final_pass) == (2, True), "two data batches, then the final pass"
    assert argv[argv.index("--nanowell_hold_dir") + 1].endswith("/keep")
    releases = [o for o in ordering if o.startswith("release:")]
    assert releases == ["release:A01", "release:A02"], "nothing held: nothing for the pass to release"
    assert [o for o in ordering if o.startswith("touch") and "_3_" in o] == [
        "touch /r/runs/run-test/.aw_batch_3_skip", "touch /r/runs/run-test/.aw_batch_3_ready"], "nothing held: skipped"


def test_a_suspend_before_the_end_runs_no_final_pass(tmp_path, monkeypatch):
    import time

    ordering: list = []
    p, proc, _built = _nanowell_batched(tmp_path, monkeypatch, ordering)
    p._runner.close_remote_job = lambda **kw: None
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    deadline = time.monotonic() + 10
    while not any("ready" in o for o in ordering) and time.monotonic() < deadline:
        time.sleep(0.01)
    p.note_suspend_requested()
    proc._done = True
    proc.batch_finished(p, 1)
    proc.say("[remote] batch-driver: suspended — no further batches\n")
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive()
    assert not any("_3_" in o or o.startswith("mv") for o in ordering), "a final pass was staged for a suspended run"
    assert not any("_3_" in o for o in ordering)
    assert [o for o in ordering if o.startswith("release:")] == ["release:A01"]


def test_the_end_of_run_pass_is_given_the_run_s_pool(tmp_path, monkeypatch):
    """What main hands the endgame must reach the pass: a pool dropped on the way runs it serially again."""
    seen: list = []
    monkeypatch.setattr(nt, "reanchor_plate", lambda *a, **k: seen.append(k.get("pool")) or {
        "anchored": [], "remaining": {}})
    monkeypatch.setattr(nt, "_check_plate_diameters", lambda *a, **k: None)
    pool = object()
    nt.nanowell_endgame({"plate_format": "100um"}, tmp_path, [], KWARGS, "P", [], "m", ["experiment"], "_", "k",
                        grouper=None, parse=None, pool=pool)
    assert seen == [pool]


def test_a_suspend_during_the_last_data_batch_runs_no_final_pass(tmp_path, monkeypatch):
    """The suspend lands while batch 2 of 2 runs: nothing breaks out of the loop, so the pass must check itself —
    staged, it would be handed to a driver that has already exited, and the run read as a Stop."""
    ordering: list = []
    p, proc, _built = _nanowell_batched(tmp_path, monkeypatch, ordering)
    p._runner.close_remote_job = lambda **kw: None
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    proc.batch_finished(p, 1)
    import time

    deadline = time.monotonic() + 10
    while not any("_2_ready" in o for o in ordering) and time.monotonic() < deadline:
        time.sleep(0.01)
    p.note_suspend_requested()
    proc.batch_finished(p, 2)
    proc.say("[remote] batch-driver: suspended — no further batches\n")
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive()
    assert not any("_3_" in o for o in ordering), "a final pass was staged for a suspended run"
    assert p._returncode == 0
    assert "suspended before the end-of-run re-anchoring" in "".join(p._logged)


def test_a_driver_lost_during_the_final_pass_is_lost_not_stopped(tmp_path, monkeypatch):
    ordering: list = []
    p, proc, _built = _nanowell_batched(tmp_path, monkeypatch, ordering)
    p._fetcher.cancel = lambda: None
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    for i in (1, 2):
        proc.batch_finished(p, i)
    proc.finish()                                   # the allocation ends before batch 3 reports
    worker.join(timeout=20)
    assert not worker.is_alive()
    assert p._driver_lost.is_set(), "the final batch is counted among the batches the driver owes"


def _bash_cache():
    """A dataset cache whose remote is this machine's ``bash``: the scripts run for real against *tmp_path*."""
    from services.remote_transport import _remote_join

    def run(script):
        done = subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, timeout=20)
        return subprocess.CompletedProcess(["bash"], done.returncode, done.stdout, done.stderr)

    return types.SimpleNamespace(_ssh_script=lambda script, *, check: run(script),
                                 _ssh_run=lambda argv, *, check, **k: run(_remote_join(argv)))


def _held(root: Path, run: str, *wells: str) -> None:
    keep = root / run / "keep"
    keep.mkdir(parents=True, exist_ok=True)
    for well in wells:
        (keep / f"{well}.zip").write_bytes(f"archive of {well}".encode())


def test_a_resumed_run_takes_over_the_wells_every_earlier_attempt_held(tmp_path, monkeypatch):
    """Tracker F4. Adoption looked only at the re-attached output's run directory, so a well held by an intermediate
    attempt (``run-2``, re-attached to ``run-1/out`` from a directory of its own) was never re-anchored; and the
    resume's own ``keep/`` was emptied before adoption ran. Now every recorded attempt is looked in, a well is taken
    when its output is in this run's output directory — what the pass re-runs it from — and the wells taken are the
    run's to release and pull again (``nanowell_adopted_holds``, tracker F3)."""
    from services import run_journal as J
    from services.remote_final_pass import adopt_earlier_holds

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    root, local = tmp_path / "remote" / "runs", tmp_path / "local"
    local.mkdir()
    out = root / "run-1" / "out"
    out.mkdir(parents=True)
    for well in ("A02", "B03", "E06", "F07"):
        (out / f"{well}_out.zip").write_bytes(b"output")
    _held(root, "run-1", "A02", "F07")             # F07 is in one of this run's batches: its batch holds it again
    _held(root, "run-2", "B03")                    # the intermediate attempt's
    _held(root, "run-x", "C04")                    # its output is not in this run's output directory: left there
    _held(root, "run-new", "D05", "E06")           # this run's own, from an attempt with the same run id
    for n, run_id in enumerate(("run-1", "run-2", "run-x")):
        J.open_run(local, spec={}, mode="remote-batched", input_dir="/in", argv=["--x"])
        J.record_remote(local, run_id=run_id, cache_key=f"k{n}", host_alias="h",
                        remote_input_dir=str(root / run_id / "in"),
                        remote_output_dir=str(out if run_id != "run-x" else root / "run-x" / "out"),
                        argv_digest="d", source_key="s")
    run = types.SimpleNamespace(remote_output_dir=str(out), nanowell_adopted_holds=set())
    logged: list[str] = []
    proc = types.SimpleNamespace(
        _final_pass=True, _fetcher=types.SimpleNamespace(run=run), _local_output_dir=local,
        _journal_keys=dict(argv_digest="d", source_key="s", host_alias="h"), _legacy_digests=(),
        _batches=[[Path("F07.zip")]], _log=logged.append,
        _runner=types.SimpleNamespace(_cache=_bash_cache(), _run=run))

    assert adopt_earlier_holds(proc, str(root / "run-new"))

    assert sorted(p.name for p in (root / "run-new" / "keep").iterdir()) == ["A02.zip", "B03.zip", "E06.zip"], (
        "the wells the earlier attempts held were not all taken into this run's keep/")
    assert run.nanowell_adopted_holds == {"A02", "B03", "E06"}
    assert (root / "run-x" / "keep" / "C04.zip").exists(), "a held well whose output is elsewhere was taken"
    assert (root / "run-1" / "keep" / "F07.zip").exists(), "a well this run's own batch runs was taken from it"
    assert "3 well(s) held by an earlier attempt" in "".join(logged)


def test_a_pass_that_does_not_finish_puts_its_wells_back_in_keep(tmp_path):
    """Tracker F4: the pass moved ``keep/`` into its batch directory and the driver swapped that into ``in/``; one
    that failed, or whose driver was lost, left the archives in either, where no later run looked."""
    from services.remote_final_pass import _back_to_keep

    run = tmp_path / "run"
    for sub, well in (("b3", "B03"), ("in", "C05")):
        (run / sub).mkdir(parents=True)
        (run / sub / f"{well}.zip").write_bytes(b"held")
    logged: list[str] = []
    proc = types.SimpleNamespace(_runner=types.SimpleNamespace(_cache=_bash_cache()), _log=logged.append)
    _back_to_keep(proc, str(run / "keep"), str(run / "b3"))
    assert sorted(p.name for p in (run / "keep").iterdir()) == ["B03.zip", "C05.zip"], logged


def _holding(tmp_path, cache):
    """A batched nanowell process re-attached to ``run-old/out``, whose remote is *cache*; returns it and its log."""
    logged: list = []
    run = types.SimpleNamespace(remote_output_dir=str(tmp_path / "runs" / "run-old" / "out"),
                                nanowell_adopted_holds=set())
    proc = types.SimpleNamespace(_final_pass=True, _log=logged.append, _fetcher=types.SimpleNamespace(run=run),
                                 _runner=types.SimpleNamespace(_cache=cache, _run=run))
    return proc, logged


def test_an_earlier_attempt_that_held_nothing_is_not_a_failure(tmp_path):
    from services.remote_final_pass import adopt_earlier_holds

    proc, logged = _holding(tmp_path, _bash_cache())
    assert adopt_earlier_holds(proc, str(tmp_path / "runs" / "run-new"))
    assert logged == [] and proc._fetcher.run.nanowell_adopted_holds == set()


def test_held_wells_that_cannot_be_adopted_stop_the_run_before_it_launches(tmp_path):
    """The move failing used to be discarded, and the earlier attempt's held wells then never got their pass."""
    from services.remote_final_pass import adopt_earlier_holds

    cache = _bash_cache()
    cache._ssh_script = lambda script, *, check: subprocess.CompletedProcess(["ssh"], 255, "", "connection reset")
    proc, logged = _holding(tmp_path, cache)
    assert adopt_earlier_holds(proc, str(tmp_path / "runs" / "run-new")) is False
    assert "ERROR could not take over the wells an earlier attempt held" in "".join(logged)
