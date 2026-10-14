"""Empty-well correction has to reach every batch, from one upload.

Asked: *"when background correction is set to use empty wells, how is it
possible that those wells never get uploaded until the end, yet are still used
for correcting every other well?"*

They were not. Batched remote mode runs one pipeline invocation per batch and
stages each batch with ``rm -rf in && mv b<i> in``, so ``input_dir`` holds only
that batch's wells. ``build_background_models`` scans that directory for the
named blanks; a batch without them builds no model, ``_empty_well_field``
returns ``None``, and the correction becomes a **no-op for every image in the
batch** — output identical to the raw input, with nothing to distinguish it from
a corrected well.

The blank is a full well of uncompressed frames, the same size as any other, so
the fix cannot be "put it in every batch". It is uploaded once to a directory of
its own and named to the pipeline with ``--background_dir``.

These tests read pixels: ``build_background_models`` estimates a field from
actual frames, so a fabricated member list would exercise none of it.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest
import tifffile

from process_microscopy import correct_single_frame
from services.remote_commands import unpack_login_shell_command
from pipeline.empty_well import (
    build_background_models,
    _empty_well_field,
)

_SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
_H = _W = 32
_YY, _XX = np.mgrid[0:_H, 0:_W]
#: A background with real structure — a flat offset would be corrected by
#: almost anything and would not tell a working field from a broken one.
_BG = (200 + 3 * _YY + 2 * _XX).astype(np.uint16)
_SIGNAL = np.zeros((_H, _W), np.uint16)
_SIGNAL[10:20, 10:20] = 500


def _plate(tmp_path: Path, wells, *, blank="H12", sub="") -> Path:
    """Per-well zips holding real TIFs. The blank carries background only."""
    root = tmp_path / (sub or "in")
    root.mkdir(parents=True, exist_ok=True)
    for well in wells:
        img = _BG if well == blank else (_BG + _SIGNAL)
        with zipfile.ZipFile(root / f"{well}.zip", "w") as zf:
            for chan in ("RFP", "GFP"):
                staging = Path(tempfile.mkdtemp()) / "f.tif"
                tifffile.imwrite(staging, img)
                zf.write(staging, f"Exp_{chan}_{well}_f0_t0.tif")
    return root


def _models(source: Path):
    return build_background_models(
        source, tokens=["RFP", "GFP"], schema=_SCHEMA, sep="_",
        background_wells=["H12"])


# ── the defect ───────────────────────────────────────────────────────────────


def test_a_batch_without_the_blank_builds_no_model(tmp_path):
    """The mechanism, pinned. Batched staging leaves most batches with no blank
    in their input directory at all."""
    whole = _plate(tmp_path, ["A01", "A02", "H12"], sub="whole")
    batch = _plate(tmp_path, ["A01", "A02"], sub="batch1")

    assert sorted(_models(whole)) == ["gfp", "rfp"]
    assert _models(batch) == {}, "a blank that is not there cannot be a reference"


def test_without_a_model_the_frame_is_returned_untouched(tmp_path):
    """Why this is not a degraded correction but *no* correction — and why it is
    invisible afterwards. The uncorrected output is bit-for-bit the raw input:
    no marker on the image, no column in the CSV, nothing in the run record."""
    raw = (_BG + _SIGNAL).astype(np.float32)

    with_blank = _empty_well_field(
        _models(_plate(tmp_path, ["A01", "H12"], sub="w")), "gfp", "0")
    without = _empty_well_field(
        _models(_plate(tmp_path, ["A01", "A02"], sub="wo")), "gfp", "0")

    corrected = correct_single_frame(raw, background_method="empty_well",
                                     bg_field=with_blank)
    uncorrected = correct_single_frame(raw, background_method="empty_well",
                                       bg_field=without)

    # The correction is real: the gradient is gone from a corner with no signal.
    assert float(np.median(corrected[0:5, 0:5])) == 0.0
    assert float(np.median(corrected[12:18, 12:18])) > 400

    # And without it, the output *is* the input.
    assert np.array_equal(uncorrected, raw), (
        "an uncorrected frame is indistinguishable from a corrected one")
    assert float(np.median(uncorrected[0:5, 0:5])) == float(np.median(raw[0:5, 0:5]))


# ── the fix: one upload, every batch corrected ───────────────────────────────


def test_the_blank_is_read_from_its_own_directory(tmp_path):
    """`--background_dir` is what lets a batch whose input holds none of the
    blanks still build the correction."""
    batch = _plate(tmp_path, ["A01", "A02"], sub="batch1")
    bg = _plate(tmp_path, ["H12"], sub="bg")

    assert _models(batch) == {}
    from_bg = build_background_models(
        batch, tokens=["RFP", "GFP"], schema=_SCHEMA, sep="_",
        background_wells=["H12"], background_dir=bg)
    assert sorted(from_bg) == ["gfp", "rfp"]


def test_the_blank_is_held_out_of_the_batches_and_uploaded_once(tmp_path):
    """The size constraint, asserted at the uploads. A blank is a full well of
    uncompressed frames, so a design that puts it in every batch multiplies the
    largest object in the run — the whole reason it gets a directory of its own
    rather than being copied N times."""

    uploads: list[tuple[str, tuple[str, ...]]] = []

    def fake_upload(zips, dest, **kw):
        uploads.append((dest.rsplit("/", 1)[-1],
                        tuple(sorted(Path(z).stem for z in zips))))
        return (0, 0.0, 0)

    proc = _FakeProc()
    p = _batched(tmp_path, ["A01", "A02", "A03", "H12"], batch_size=1)
    _stub_runner(p, proc, fake_upload)
    p._pipeline_argv = ["--background", "empty_well", "--background_well", "H12"]

    import unittest.mock as mock
    with mock.patch.object(subprocess, "Popen", lambda argv, **kw: proc):
        import threading
        worker = threading.Thread(target=p._run_batches, daemon=True)
        worker.start()
        # Generous: this branch slices 3 batches (the blank is held out) and
        # pre-fix code slices 4. Feeding both keeps the proof against the base
        # branch measuring *where* the blank went rather than stalling on a
        # marker that never comes.
        for i in (1, 2, 3, 4):
            proc.say(f"[remote] batch-driver: batch {i} finished rc=0\n")
        proc.finish()
        worker.join(timeout=20)

    blank_uploads = [d for d, stems in uploads if "H12" in stems]
    assert blank_uploads == ["bg"], (
        f"the blank crossed the wire {len(blank_uploads)} time(s), to "
        f"{blank_uploads} — it must be uploaded once, to its own directory")
    # …and the batches carry only the sample wells.
    # `bg` also starts with "b" — match the batch dirs by their real shape.
    batch_stems = [stems for d, stems in uploads if re.fullmatch(r"b\d+", d)]
    assert all("H12" not in stems for stems in batch_stems), batch_stems
    assert sorted(s for stems in batch_stems for s in stems) == \
        ["A01", "A02", "A03"]


def test_a_re_run_still_uploads_the_blank_it_no_longer_needs_to_process(tmp_path):
    """The half that is easy to miss, and did get missed.

    Batched mode skips wells whose per-well CSV is already complete locally. A
    blank processed by an earlier run has one — so on a re-run the filter drops
    it before anything sees it as a blank, no reference is uploaded, and **no**
    batch gets correction, including the last. Worse than the original defect,
    which at least corrected one batch.

    The blank is input to the correction, not a well whose output is wanted, so
    the filter's verdict must not reach it. It is still not re-processed: it is
    uploaded as a reference and left out of the final batch's link.
    """

    uploads: list[tuple[str, tuple[str, ...]]] = []

    def fake_upload(zips, dest, **kw):
        uploads.append((dest.rsplit("/", 1)[-1],
                        tuple(sorted(Path(z).stem for z in zips))))
        return (0, 0.0, 0)

    proc = _FakeProc()
    p = _batched(tmp_path, ["A01", "A02", "H12"], batch_size=1)
    _stub_runner(p, proc, fake_upload)
    p._pipeline_argv = ["--background", "empty_well", "--background_well", "H12"]

    # An earlier run finished the blank *and* one sample well.
    out = tmp_path / "out"
    out.mkdir()
    p._local_output_dir = out
    for well in ("H12", "A02"):
        (out / f"measurements_{well}.csv").write_text("well\n" + well + "\n")

    drivers: list[str] = []
    import unittest.mock as mock
    with mock.patch.object(subprocess, "Popen",
                           lambda argv, **kw: drivers.append(
                               unpack_login_shell_command(argv[-1])) or proc):
        import threading
        worker = threading.Thread(target=p._run_batches, daemon=True)
        worker.start()
        for i in (1, 2):
            proc.say(f"[remote] batch-driver: batch {i} finished rc=0\n")
        proc.finish()
        worker.join(timeout=20)

    assert [d for d, _ in uploads if d == "bg"] == ["bg"], (
        f"the blank was not uploaded as a reference: {uploads}")
    # The ordinary complete well is still skipped, which is the behaviour the
    # filter exists for — only the blank is exempt.
    uploaded = {stem for d, stems in uploads if re.fullmatch(r"b\d+", d)
                for stem in stems}
    assert uploaded == {"A01"}, uploaded
    # And it is not linked back in for processing: the user's re-run declined it.
    assert drivers and "/bg/" not in drivers[0], (
        "a blank the re-run skipped was linked in and reprocessed")


def test_the_pipeline_is_told_where_the_blank_is(tmp_path):
    """A one-line omission here would put the blank on the remote and leave
    every batch still unable to find it."""
    from services.remote_runner import RemoteRunner
    from services.remote_config import RemoteHost

    runner = object.__new__(RemoteRunner)
    runner.host = RemoteHost(name="h", ssh_alias="h")
    runner.env_selector = ("name", "allwell")
    argv = ["py", "process_microscopy.py", "--input_dir", "/local/in",
            "--output_dir", "/local/out", "--background", "empty_well"]
    from services.remote_runner import LocalInputMode, OutputFilter

    out = runner._build_remote_argv(
        argv, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP,
        background_dir="/r/runs/x/bg")
    assert "--background_dir=/r/runs/x/bg" in out

    # Not present on every other path, where the whole plate is in --input_dir.
    plain = runner._build_remote_argv(
        argv, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP)
    assert not any(a.startswith("--background_dir") for a in plain)


@pytest.mark.parametrize("argv,expected", [
    (["--background", "empty_well", "--background_well", "H12"], ("H12",)),
    (["--flatfield", "empty_well", "--background_well", "h12"], ("H12",)),
    (["--background", "empty_well", "--background_well", "H12,G12"],
     ("G12", "H12")),
    # No empty-well correction asked for: nothing is held out of the batches.
    (["--background", "top_hat", "--background_well", "H12"], ()),
    # Asked for, but no well named — the GUI refuses this; nothing to hold out.
    (["--background", "empty_well"], ()),
])
def test_which_wells_are_treated_as_blanks(argv, expected, tmp_path):
    from services.remote_process import _empty_well_blanks

    zips = [tmp_path / f"{w}.zip" for w in ("A01", "G12", "H12")]
    assert tuple(sorted(_empty_well_blanks(argv, zips))) == expected


# ── refusing to produce numbers that claim a correction they never got ───────


def test_a_requested_correction_that_cannot_be_built_stops_the_run():
    """Uncorrected output that looks corrected is the one thing this codebase
    is not allowed to emit, so a model that comes back empty is fatal rather
    than a log line nobody reads."""
    from pipeline.empty_well import _require_empty_well_models

    with pytest.raises(SystemExit) as exc:
        _require_empty_well_models("background", "empty_well", {},
                                   ["H12"], Path("/r/runs/x/bg"))
    msg = str(exc.value)
    assert "H12" in msg
    assert "/r/runs/x/bg" in msg, "the message has to name where it looked"
    assert "background_dir" in msg


def test_a_built_model_and_the_unrelated_modes_pass(tmp_path):
    """The gate must not fire on a healthy run, nor on a mode that never had a
    blank to look for."""
    from pipeline.empty_well import _require_empty_well_models

    _require_empty_well_models("background", "empty_well", {"gfp": {}},
                               ["H12"], tmp_path)          # built: fine
    _require_empty_well_models("background", "top_hat", {}, ["H12"], tmp_path)
    _require_empty_well_models("flat-field", "basic", {}, [], tmp_path)
    # Asked for, no well named — a configuration error the GUI already refuses;
    # restating it here would only say it worse.
    _require_empty_well_models("background", "empty_well", {}, [], tmp_path)


# ── local harness, shared with the batch-driver tests ────────────────────────

import queue  # noqa: E402
import threading  # noqa: E402
import types  # noqa: E402

from services.remote_process import _BatchedRemoteProcess


class _FakeProc:
    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self.returncode = 0
        self._done = False
        self.pid = 4242

    def say(self, line):
        self._q.put(line)

    def finish(self):
        self._done = True
        self._q.put(None)

    @property
    def stdout(self):
        def _iter():
            while True:
                item = self._q.get()
                if item is None:
                    return
                yield item
        return _iter()

    def poll(self):
        return self.returncode if self._done else None

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass


def _batched(tmp_path, wells, batch_size=1):
    p = object.__new__(_BatchedRemoteProcess)
    zips = []
    for well in wells:
        z = tmp_path / f"{well}.zip"
        z.write_bytes(b"PK\x05\x06" + b"\0" * 18)
        zips.append(z)
    p._zipped = types.SimpleNamespace(well_zips=zips, incremental=False, mode=None)
    p._local_output_dir = tmp_path
    p._pipeline_argv = []
    p._batch_size = batch_size
    p._line_q = queue.Queue()
    p._logged = []
    p._log = lambda m: p._logged.append(m)
    p._returncode = None
    p._stop = threading.Event()
    p._suspending = threading.Event()
    p._current_proc_lock = threading.Lock()
    p._current_proc = None
    p._batch_events = {}
    p._batch_rc = {}
    p._stream_ended = threading.Event()
    p._driver_lost = threading.Event()
    p._n_batches = 0
    p._owns_allocation = False
    p._reader = None
    p._heartbeat = None
    p._run_id = "run-test"
    p._output_filter = None
    p._progress = None
    p._cancelled = []
    p._fetcher = types.SimpleNamespace(
        run=types.SimpleNamespace(well_count_total=0, fetched_wells=set()),
        observe=lambda line: None,
        flush_and_join=lambda *a, **k: None,
        cancel=lambda: p._cancelled.append(True),
    )
    p._run_local_auto_threshold = lambda: None
    p._auto_threshold_off_by_user = lambda: True
    return p


def _stub_runner(p, proc, upload_subset):
    p._runner = types.SimpleNamespace(
        _cache=types.SimpleNamespace(
            _ssh_run=lambda argv, *, check, **kw: types.SimpleNamespace(
                stdout="", returncode=0),
            upload_subset=upload_subset),
        _run=types.SimpleNamespace(
            remote_input_dir="/r/run-test/in",
            remote_output_dir="/r/run-test/out",
            remote_pidfile="/r/run-test/run.pid"),
        host=types.SimpleNamespace(
            remote_staging_dir="/r", compute_launcher="srun --time=1:00:00",
            remote_scratch_dir="", ssh_argv=lambda **kw: ["ssh", "h"]),
        env_selector=("name", "allwell"),
        _build_remote_argv=lambda *a, **k: ["python", "pm.py"],
        _sweep_orphaned_allocation=lambda *a, **k: None,
    )
    return proc


# ── what an adversarial review of the first two commits turned up ────────────


def test_an_incremental_re_run_stages_the_blank_whole(tmp_path):
    """The severest of them. The incremental split drops a well whose every FOV
    is already processed — which is the normal state of a blank on every re-run,
    and it `unlink()`s the staged copy on the way out. The blank then vanished
    before anything could treat it as a reference: no upload, no
    `--background_dir`, and with the new guard in place the run *aborted*
    instead of merely producing uncorrected numbers.

    A partial blank would be worse than either: the split rewrites a well that
    has *some* new FOVs down to those members, so the field would be estimated
    from a fraction of the reference while the log reported it as built.
    """
    from services.remote_staging import (
        _plan_staging,
        _required_tokens,
    )
    from util.well_token import background_well_key

    prior = tmp_path / "prior"
    prior.mkdir()
    src = tmp_path / "in"
    src.mkdir()
    for well in ("A01", "H12"):
        with zipfile.ZipFile(src / f"{well}.zip", "w") as zf:
            for chan in ("RFP", "GFP"):
                for fov in ("f0", "f1"):
                    zf.writestr(f"Exp_{chan}_{well}_{fov}_t0.tif", b"x" * 400)
        (prior / f"measurements_{well}.csv").write_text(
            "well,fov,timepoint\n"
            + "".join(f"{well},{f},t0\n" for f in ("f0", "f1")))
    with zipfile.ZipFile(src / "A01.zip", "a") as zf:      # one new field
        for chan in ("RFP", "GFP"):
            zf.writestr(f"Exp_{chan}_A01_f2_t0.tif", b"x" * 400)

    def plan(background_wells):
        plans, up_to_date, _ = _plan_staging(
            tuple(sorted(src.glob("*.zip"))),
            keep_tokens=_required_tokens("RFP", ["GFP"]),
            incremental=True, prior_output_dir=prior,
            filename_schema="experiment:channel:well:fov:timepoint",
            filename_sep="_", background_wells=background_wells)
        return {p.src.stem: p for p in plans}, list(up_to_date)

    without, up = plan(frozenset())
    assert "H12" not in without and up == ["H12"], (
        "characterising the defect: the blank is dropped as up to date")

    with_exempt, up = plan(frozenset({background_well_key("H12")}))
    assert "H12" in with_exempt, "the reference was diffed away"
    assert with_exempt["H12"].keep is None, (
        "the blank was thinned to its new FOVs — the field would be estimated "
        "from a fraction of the reference")
    assert up == []
    # And the exemption is narrow: an ordinary well is still diffed.
    assert with_exempt["A01"].keep is not None


def test_with_nothing_but_blanks_left_the_run_still_completes(tmp_path):
    """Every sample well complete locally. Holding the blank out would leave an
    empty batch, uploaded to a directory the driver then cannot `mv`, ending the
    run at 90 with the blank never processed. It is treated as an ordinary well
    instead — it corrects itself, as a single-shot run of it alone would — and
    it still crosses the wire exactly once."""
    uploads, _drivers, p, _lines = _drive(
        tmp_path, ["A01", "A02", "H12"], complete=("A01", "A02"))

    assert p._returncode == 0, p._logged
    crossings = [d for d, stems in uploads if "H12" in stems]
    assert len(crossings) == 1, f"the blank crossed the wire {crossings}"


def test_only_a_blank_that_still_needs_processing_is_linked_back_in(tmp_path):
    """`--background_well G12,H12` with G12 already complete. A single flag for
    all blanks, or a glob over `bg/*.zip`, reprocesses the well the user's own
    re-run just declined."""
    _uploads, drivers, _p, _lines = _drive(
        tmp_path, ["A01", "A02", "G12", "H12"], complete=("G12",),
        argv=["--background", "empty_well", "--background_well", "G12,H12"])

    assert drivers, "no driver was composed"
    assert "/bg/H12.zip" in drivers[0]
    assert "/bg/G12.zip" not in drivers[0], (
        "a blank the re-run declined was linked back in and reprocessed")


def test_a_reference_only_blank_does_not_sit_painted_as_uploading(tmp_path):
    """Its SKIP marker lands first, then the reference upload's own per-well
    line repaints the cell "uploading" — and nothing moves it off, because the
    well is never processed or downloaded. The count stays truthful; only the
    colour lies, which is its own kind of silent wrong."""
    _uploads, _drivers, _p, lines = _drive(
        tmp_path, ["A01", "A02", "H12"], complete=("H12",))

    settled = [ln for ln in lines if "reference only" in ln]
    assert settled, "the reference-only blank is left painted mid-upload"
    assert "SKIP well H12" in settled[0]


def test_a_stop_during_the_reference_upload_is_not_a_fault(tmp_path):
    """`upload_subset` watches the stop event, so a Stop returns a non-zero
    rsync code. Reporting the user's own Stop as a failure — with rsync's number
    rather than 130 — is what the batch-upload path already avoids."""
    import unittest.mock as mock

    proc = _FakeProc()
    p = _batched(tmp_path, ["A01", "H12"], batch_size=1)

    def stopping_upload(zips, dest, *, stop_event=None, **kw):
        stop_event.set()                    # the user clicks Stop mid-transfer
        return (0, 0.0, 20)

    _stub_runner(p, proc, stopping_upload)
    p._pipeline_argv = ["--background", "empty_well", "--background_well", "H12"]

    with mock.patch.object(subprocess, "Popen", lambda a, **k: proc):
        p._run_batches()

    assert p._returncode == 130, p._logged
    joined = "".join(p._logged)
    assert "stopped while uploading" in joined
    assert "ERROR" not in joined, "a Stop the user asked for is not a fault"


def test_the_blank_list_is_parsed_the_way_the_pipeline_parses_it(tmp_path):
    """A local `re.split(r"[,\\s]+", …)` read `"H12 G12"` as two wells where the
    pipeline's own `split_background_wells` reads it as one — so batched mode
    held out and uploaded two archives the pipeline then matched neither of, and
    the run died on the guard in every batch. One parser, one answer."""
    from services.remote_process import _empty_well_blanks
    from util.well_token import background_well_key, split_background_wells

    zips = [tmp_path / f"{w}.zip" for w in ("A01", "G12", "H12")]
    for spelling in ("H12 G12", "H12;G12", "H12,G12", "h12"):
        argv = ["--background", "empty_well", "--background_well", spelling]
        held_out = set(_empty_well_blanks(argv, zips))
        pipeline_sees = {background_well_key(w)
                         for w in split_background_wells(spelling)}
        assert {background_well_key(s) for s in held_out} <= pipeline_sees, (
            f"{spelling!r}: staging holds out {held_out}, pipeline matches "
            f"{pipeline_sees}")


def _drive(tmp_path, wells, *, complete=(), argv=None, batch_size=1):
    """Run `_run_batches` with the remote stubbed; return uploads, driver cmds,
    the object, and whatever reached the stdout queue."""
    import unittest.mock as mock

    uploads: list[tuple[str, tuple[str, ...]]] = []
    drivers: list[str] = []

    def fake_upload(zips, dest, **kw):
        uploads.append((dest.rsplit("/", 1)[-1],
                        tuple(sorted(Path(z).stem for z in zips))))
        return (0, 0.0, 0)

    proc = _FakeProc()
    p = _batched(tmp_path, wells, batch_size=batch_size)
    _stub_runner(p, proc, fake_upload)
    p._pipeline_argv = argv or ["--background", "empty_well",
                                "--background_well", "H12"]
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    p._local_output_dir = out
    for well in complete:
        (out / f"measurements_{well}.csv").write_text(f"well\n{well}\n")

    with mock.patch.object(subprocess, "Popen",
                           lambda a, **k: drivers.append(
                               unpack_login_shell_command(a[-1])) or proc):
        import threading
        worker = threading.Thread(target=p._run_batches, daemon=True)
        worker.start()
        for i in range(1, 7):
            proc.say(f"[remote] batch-driver: batch {i} finished rc=0\n")
        proc.finish()
        worker.join(timeout=20)

    lines = []
    while not p._line_q.empty():
        item = p._line_q.get()
        if item:
            lines.append(item)
    return uploads, drivers, p, lines
