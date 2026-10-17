"""Disk-capacity accounting: the preflight, the mid-run projection, ENOSPC text.

The bug behind these: a local zip-mode run died with a bare ``OSError: [Errno
28] No space left on device`` while compressing ``<well>_out.zip``, on a machine
whose dataset volume showed roughly ten times the input archives' size free. Two
things had to be true at once for that to happen silently.

* **The up-front preflight never ran.** It sized the run from the first loose
  image under the input directory, and a zip-mode dataset (``in/<WELL>.zip`` —
  the layout the pipeline actually segments from) has none, so it returned
  without checking anything at all.
* **"Ten times the inputs" is not a measure of anything.** The archives are the
  only compressed copy in the run: the staging holds ~6 float32 intermediates
  per FOV-group for every well running at once, and every ``<well>_out.zip``
  produced so far stays on the output volume for the rest of the plate.
"""

from __future__ import annotations

import errno
import io as _io
import logging
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest
import tifffile

from pipeline.capacity import (
    OutputCapacityMonitor,
    estimate_group_scratch_bytes,
    preflight_scratch_for_run,
    preflight_scratch_space,
    project_output_shortfall,
    sample_image_bytes,
)

IMAGE_EXTS = {".tif", ".tiff"}


def _tif_bytes(shape=(256, 256)) -> bytes:
    buf = _io.BytesIO()
    # Random pixels, not zeros: an all-zero frame deflates to almost nothing, so
    # a fixture built from one would make the compressed/uncompressed gap this
    # module is about look thousands of times larger than any real dataset.
    rng = np.random.default_rng(0)
    tifffile.imwrite(buf, rng.integers(0, 4096, shape, dtype=np.uint16))
    return buf.getvalue()


def _zip_mode_input(root: Path, wells=("A01", "A02"), channels=("w1594", "w2")) -> Path:
    """An ``in/`` directory holding one archive per well and no loose images."""
    in_dir = root / "in"
    in_dir.mkdir(parents=True, exist_ok=True)
    for well in wells:
        with zipfile.ZipFile(in_dir / f"{well}.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            # The mess real archives carry: a Finder sidecar and a directory
            # entry alongside the frames, so the sampler has to ignore them.
            zf.writestr(f"{well}/", b"")
            zf.writestr(".DS_Store", b"\x00" * 64)
            for ch in channels:
                zf.writestr(f"{well}_F001_T001_{ch}.tif", _tif_bytes())
    return in_dir


# ── Sizing the dataset ──────────────────────────────────────────────────────

def test_sample_image_bytes_reads_a_zip_mode_dataset(tmp_path):
    """The measurement the run was missing: an archive's uncompressed member."""
    in_dir = _zip_mode_input(tmp_path)
    sample = sample_image_bytes(in_dir, IMAGE_EXTS)
    assert sample > 0, "a zip-mode dataset must be measurable without extracting"
    with zipfile.ZipFile(in_dir / "A01.zip") as zf:
        frames = [i for i in zf.infolist() if i.filename.endswith(".tif")]
    assert sample == max(i.file_size for i in frames), (
        "the uncompressed member size is what gets staged, and it is the number "
        "the run has to be sized from"
    )
    assert sample >= max(i.compress_size for i in frames), (
        "sizing a run from what the archive occupies on disk is the mistake "
        "this measurement exists to avoid"
    )


def test_sample_image_bytes_prefers_the_largest_member(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    with zipfile.ZipFile(in_dir / "A01.zip", "w") as zf:
        zf.writestr("A01_F001_T001_thumb.tif", _tif_bytes((32, 32)))
        zf.writestr("A01_F001_T001_w1.tif", _tif_bytes((512, 512)))
    small = len(_tif_bytes((32, 32)))
    assert sample_image_bytes(in_dir, IMAGE_EXTS) > small


def test_sample_image_bytes_prefers_a_loose_image_and_survives_junk(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "A01_F001_T001_w1.tif").write_bytes(_tif_bytes())
    (in_dir / "notes.txt").write_text("not an image")
    (in_dir / "broken.zip").write_bytes(b"not a zip")
    assert sample_image_bytes(in_dir, IMAGE_EXTS) == len(_tif_bytes())


def test_sample_image_bytes_is_zero_when_nothing_is_measurable(tmp_path):
    (tmp_path / "in").mkdir()
    assert sample_image_bytes(tmp_path / "in", IMAGE_EXTS) == 0


# ── The preflight the failing run never reached ─────────────────────────────

def test_preflight_speaks_up_for_a_zip_mode_dataset(tmp_path, monkeypatch, caplog):
    """The regression: a zip-mode run on a volume with no room said nothing.

    Free space is faked through ``shutil.disk_usage`` rather than a module
    attribute so this test measures the *behaviour* on any version of the
    preflight, not the internals of one of them.
    """
    in_dir = _zip_mode_input(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda _p: shutil._ntuple_diskusage(total=1 << 30, used=1 << 30, free=1 << 20),
    )
    log = logging.getLogger("test_preflight_zip_mode")
    with caplog.at_level(logging.WARNING, logger=log.name):
        with pytest.raises(SystemExit):
            preflight_scratch_for_run(
                scratch, in_dir,
                n_wells=2, n_channels=2, workers=2,
                image_exts=IMAGE_EXTS, count_groups=lambda: 10, log=log,
            )
    assert "space" in caplog.text.lower(), (
        "a zip-mode run with 1 MiB free must be refused, not started silently"
    )


def test_preflight_sums_scratch_and_output_on_one_volume(tmp_path, monkeypatch):
    """Scratch alone fits; scratch plus the plate's results does not."""
    import pipeline.capacity as cap

    sample = 4_000_000
    groups, wells, workers = 10, 96, 4
    scratch_need = estimate_group_scratch_bytes(sample, 2) * groups * workers
    monkeypatch.setattr(cap, "free_bytes", lambda _p: int(scratch_need * 1.5))
    monkeypatch.setattr(cap, "same_volume", lambda _a, _b: True)

    ok, msg = preflight_scratch_space(
        tmp_path, sample_image_bytes=sample, n_channels=2,
        groups_per_well=groups, concurrent_wells=workers,
        output_dir=tmp_path / "out", n_wells=wells,
    )
    assert ok is True, "a tight volume warns; only 'not one well' refuses"
    assert "BOTH" in msg and "accumulate" in msg


def test_preflight_ignores_the_output_when_it_is_a_separate_volume(tmp_path, monkeypatch):
    import pipeline.capacity as cap

    sample = 4_000_000
    groups, workers = 10, 4
    scratch_need = estimate_group_scratch_bytes(sample, 2) * groups * workers
    monkeypatch.setattr(cap, "free_bytes", lambda _p: scratch_need * 100)
    monkeypatch.setattr(cap, "same_volume", lambda _a, _b: False)

    ok, msg = preflight_scratch_space(
        tmp_path, sample_image_bytes=sample, n_channels=2,
        groups_per_well=groups, concurrent_wells=workers,
        output_dir=tmp_path / "out", n_wells=96,
    )
    assert (ok, msg) == (True, ""), "both volumes have room; nothing to say"


# ── Mid-run projection ──────────────────────────────────────────────────────

def test_projection_warns_when_the_remaining_wells_will_not_fit(tmp_path):
    msg = project_output_shortfall(
        written=4 * (1 << 30), wells_done=4, wells_total=96,
        free=30 * (1 << 30), output_dir=tmp_path,
    )
    assert msg and "92 well(s)" in msg and "--incremental" in msg


def test_projection_is_silent_when_the_plate_fits(tmp_path):
    assert project_output_shortfall(
        written=4 * (1 << 30), wells_done=4, wells_total=96,
        free=500 * (1 << 30), output_dir=tmp_path,
    ) is None


@pytest.mark.parametrize("kwargs", [
    {"wells_done": 0, "wells_total": 96, "written": 1 << 30},     # nothing measured
    {"wells_done": 4, "wells_total": 4, "written": 1 << 30},      # plate finished
    {"wells_done": 4, "wells_total": 96, "written": 0},           # wrote nothing
])
def test_projection_says_nothing_without_a_measurement(tmp_path, kwargs):
    assert project_output_shortfall(
        free=1, output_dir=tmp_path, **kwargs) is None


def test_projection_says_nothing_when_free_space_is_unknowable(tmp_path):
    assert project_output_shortfall(
        written=1 << 40, wells_done=1, wells_total=96, free=None,
        output_dir=tmp_path,
    ) is None


def test_monitor_warns_once_and_only_while_wells_remain(tmp_path, monkeypatch):
    import pipeline.capacity as cap

    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(cap, "free_bytes", lambda _p: 1)

    warnings: list[str] = []

    class _Log:
        def warning(self, fmt, *args):
            warnings.append(fmt % args)

    monitor = OutputCapacityMonitor(out, total_wells=8, log=_Log())
    (out / "A01_out.zip").write_bytes(b"x" * 4096)
    for _ in range(4):
        monitor.well_done()
    assert len(warnings) == 1, "one warning, not one per remaining well"
    assert "will not hold the rest of this plate" in warnings[0]

    quiet = OutputCapacityMonitor(out, total_wells=1, log=_Log())
    quiet.well_done()
    assert len(warnings) == 1, "the last well has no remainder to project"


def test_monitor_measures_only_what_this_run_wrote(tmp_path, monkeypatch):
    """Output already in the folder is not this run's per-well cost.

    The reported case: a 12-well batch of a 60-well plate, one well finished,
    and the log said the 11 left needed 2057.9 GiB "at the 187.1 GiB per well
    measured from the 1 already written" — the earlier batches' results in the
    same folder, divided by one well. Here the folder starts with 48 wells of
    earlier output and this run's first well writes one more; the projection
    must be 11 x one well, not 11 x 49.
    """
    import pipeline.capacity as cap

    gib = 1 << 30
    out = tmp_path / "out"
    out.mkdir()
    sizes = {"A": 0}

    def fake_dir_bytes(_p):
        return sizes["A"]

    monkeypatch.setattr(cap, "directory_bytes", fake_dir_bytes)
    monkeypatch.setattr(cap, "free_bytes", lambda _p: 10 * gib)
    sizes["A"] = 48 * 4 * gib            # earlier batches, already on disk

    warnings: list[str] = []

    class _Log:
        def warning(self, fmt, *args):
            warnings.append(fmt % args)

    monitor = OutputCapacityMonitor(out, total_wells=12, log=_Log())
    sizes["A"] += 4 * gib                # this run's first well
    monitor.well_done()
    assert len(warnings) == 1
    assert "about 44.0 GiB at the 4.0 GiB per well" in warnings[0], warnings[0]


def test_monitor_projects_nothing_when_the_folder_shrank(tmp_path, monkeypatch):
    """A Fetcher deleting downloaded archives must not read as negative output."""
    import pipeline.capacity as cap

    sizes = {"A": 100 << 30}
    monkeypatch.setattr(cap, "directory_bytes", lambda _p: sizes["A"])
    monkeypatch.setattr(cap, "free_bytes", lambda _p: 1)
    warnings: list[str] = []

    class _Log:
        def warning(self, fmt, *args):
            warnings.append(fmt % args)

    monitor = OutputCapacityMonitor(tmp_path, total_wells=12, log=_Log())
    sizes["A"] = 1 << 30
    monitor.well_done()
    assert warnings == []


# ── What the user sees when a volume fills anyway ───────────────────────────

def test_enospc_names_the_volume_and_keeps_its_errno():
    """A bare '[Errno 28]' sends the user to check the wrong disk."""
    from util.retry_io import retry_io

    target = Path("/nonexistent-volume/out/B04_out.zip")

    def _boom():
        raise OSError(errno.ENOSPC, "No space left on device")

    with pytest.raises(OSError) as excinfo:
        retry_io(_boom, what="B04_out.zip", path=target, base_delay=0)

    exc = excinfo.value
    assert exc.errno == errno.ENOSPC, "callers matching on the errno must still work"
    text = str(exc)
    assert str(target.parent) in text, "the message must name the volume that filled"
    assert "ALLWELL_SCRATCH_DIR" in text and "--incremental" in text


def test_enospc_is_not_retried():
    from util.retry_io import retry_io

    attempts = []

    def _boom():
        attempts.append(1)
        raise OSError(errno.ENOSPC, "No space left on device")

    with pytest.raises(OSError):
        retry_io(_boom, what="thing", path=Path("/tmp/thing"), base_delay=0)
    assert attempts == [1], "a full volume does not empty itself between retries"


def test_imwrite_reports_enospc_from_the_codec(tmp_path, monkeypatch):
    """tifffile can raise ENOSPC directly; that must not be read as transient."""
    import pipeline.io as pio

    monkeypatch.setattr(pio, "_RETRY_BASE_DELAY_S", 0)

    def _full(*_a, **_k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(pio, "imwrite", _full)
    with pytest.raises(OSError) as excinfo:
        pio._safe_imwrite(tmp_path / "img.tif", np.zeros((4, 4), np.uint16))
    assert excinfo.value.errno == errno.ENOSPC
    assert str(tmp_path) in str(excinfo.value)


# ── the refusal that stopped a real run, and the two defects behind it ───────
#
# Reported: clicking Run showed progress reach Grouped and then drop straight
# back to an idle button, with pipeline_info.json written and nothing said. The
# pipeline had refused to start:
#
#   ERROR  Not enough scratch space to process even one well: 4.9 GiB free on
#          /Users/…/.allwell/scratch — the run needs about 20.8 GiB there
#          (4 well(s) at once, 10 FOV-group(s) each). Exiting before …
#
# On a machine whose Finder reported 14.3 GB free.

_SAMPLE = 29 * 1024 ** 2          # largest member of the reported plate
_FREE = int(4.9 * 1024 ** 3)      # what statvfs reported on that Mac


def _preflight(monkeypatch, *, n_channels, free=_FREE, **over):
    from pipeline import capacity

    monkeypatch.setattr(capacity, "free_bytes", lambda _p: free)
    kwargs = dict(sample_image_bytes=_SAMPLE, n_channels=n_channels,
                  groups_per_well=10, concurrent_wells=4,
                  output_dir=None, n_wells=4)
    kwargs.update(over)
    return capacity.preflight_scratch_space(Path("/scratch"), **kwargs)


def test_one_phantom_channel_is_the_difference_between_refuse_and_allow(monkeypatch):
    """What the off-by-one was worth, at the reported plate's scale.

    Characterisation, not a guard: ``capacity.py``'s arithmetic is unchanged by
    the fix, so this passes on either side of it. It exists to pin how much a
    single phantom channel moves the verdict — one frame per FOV-group, which at
    five channels and ten groups is the whole margin. The guard that bites is
    ``test_main_passes_the_real_channel_count``, which watches the call site
    where both numbers are in scope.

    The sample size here is chosen, not measured off the reported plate, so the
    GiB figures are this fixture's and not that machine's.
    """
    refused, _ = _preflight(monkeypatch, n_channels=6)     # the old count
    allowed, message = _preflight(monkeypatch, n_channels=5)
    assert refused is False
    assert allowed is True
    assert "tight" in message      # still warns; it is a small volume


def test_main_passes_the_real_channel_count(monkeypatch, tmp_path):
    """The off-by-one is invisible inside capacity.py — only the call site has
    both numbers — so the guard has to watch what ``main()`` hands over."""
    import sys

    import process_microscopy as pm

    seen = {}

    def _spy(*_a, **kw):
        seen.update(kw)
        raise SystemExit(99)

    monkeypatch.setattr(pm, "preflight_scratch_for_run", _spy)
    (tmp_path / "A01.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py",
        "--input_dir", str(tmp_path), "--output_dir", str(tmp_path / "out"),
        "--nuclear_token", "w1594",
        "--fluor_tokens", "DIC", "w2Turq", "w3Cit", "w4Cy5",
        "--filename_schema", "well:channel:timepoint:ignore:ignore",
    ])
    with pytest.raises(SystemExit):
        pm.main()
    # w1594 + the four fluor tokens. Not six.
    assert seen["n_channels"] == 5


def test_the_refusal_quotes_the_figure_it_tested(monkeypatch):
    """It fires on ``free < per_well`` and used to print ``scratch_need`` — the
    whole-run figure for every well staging at once. The user was told the run
    wanted 20.8 GiB when what failed the comparison was the 5.1 GiB one well
    needs, and the number they could have acted on never appeared."""
    ok, message = _preflight(monkeypatch, n_channels=6)
    assert ok is False
    assert "one well needs about 5.1 GiB" in message
    assert "20.4 GiB" in message and "4 well(s) staging at once" in message
    # And it says what the free figure means, because it will not match Finder.
    assert "purgeable" in message
    assert "--allow_low_disk" in message


def test_allow_low_disk_downgrades_the_refusal(monkeypatch, tmp_path, caplog):
    """A conservative estimate must never be able to lock someone out of their
    own data with no way past it."""
    from pipeline import capacity

    monkeypatch.setattr(capacity, "free_bytes", lambda _p: 1)
    log = logging.getLogger("test_allow_low_disk")
    (tmp_path / "A01.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)

    def _run(allow):
        return capacity.preflight_scratch_for_run(
            tmp_path, tmp_path, n_wells=1, n_channels=2, workers=1,
            image_exts=(".tif",), count_groups=lambda: 1, log=log,
            allow_low_disk=allow,
        )

    monkeypatch.setattr(capacity, "sample_image_bytes", lambda *_a, **_k: 10 ** 9)
    with pytest.raises(SystemExit) as exc:
        _run(False)
    assert exc.value.code == 2

    # Continuing is not the same as continuing quietly. The override exists so
    # a wrong estimate cannot wedge someone out of their data — not so a real
    # shortfall can pass unremarked, which is the substitution this repo's
    # rules exist to prevent.
    with caplog.at_level(logging.WARNING, logger=log.name):
        assert _run(True) is None      # returns instead of exiting
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Not enough scratch space" in m and "allow_low_disk" in m
               for m in msgs), msgs


# ── the exit had to reach the user, not just happen ─────────────────────────

def test_the_exit_message_carries_the_pipelines_own_reason():
    """"Pipeline exited with code 2." names nothing, and for a run refused
    before any work it was the *only* thing shown. The pipeline always explains
    itself on stdout one line earlier."""
    from services.pipeline_runner import exit_message

    tail = [
        "2026-08-08 19:10:32  INFO      Found 4 well zip file(s) in /x/in",
        "2026-08-08 19:10:32  INFO      Scratch  : /Users/z/.allwell/scratch",
        "2026-08-08 19:10:32  ERROR     Not enough scratch space to process "
        "even one well: 4.9 GiB free. Exiting before processing anything.",
    ]
    msg = exit_message(2, tail)
    assert "code 2" in msg
    assert "Not enough scratch space" in msg


def test_the_exit_message_never_invents_a_cause():
    """With no line that classifies as an error there is nothing to quote.

    Presenting the last line whatever it is makes a routine progress line into
    the stated reason a run stopped — "quantify: 1.2 s" is not why anything
    failed, and a Stop produces exactly that shape. Severity comes from
    ``classify_log_line`` rather than a substring test, because the pipeline
    writes its tracebacks to a directory named ``errors/`` and its clean summary
    says "wells failed : 0"; a substring test reads both as failures.
    """
    from services.pipeline_runner import exit_message

    bare = "Pipeline exited with code 1."
    assert exit_message(1, []).strip() == bare
    assert exit_message(1, None).strip() == bare
    # A stopped run: two routine INFO lines, nothing to blame.
    assert exit_message(1, [
        "2026-08-08 19:10:32  INFO      Well A01 — starting (pid 4)",
        "2026-08-08 19:10:32  INFO      quantify:    1.2 s  (431 nuclei)",
    ]).strip() == bare
    # A path naming errors/ is not an error, and neither is a "note:".
    assert exit_message(1, [
        "[remote] note: out/errors/ not present on remote",
        "2026-08-08 19:10:32  INFO      Fetched 96 well(s)",
    ]).strip() == bare
    # And when there IS a real error line, the summary that mentions errors/
    # afterwards must not displace it.
    got = exit_message(2, [
        "2026-08-08 19:10:32  ERROR     real cause: no wells found",
        "2026-08-08 19:10:32  INFO      wrote /out/ERROR summary to errors/",
    ])
    assert got.startswith("real cause: no wells found")
    assert "code 2" in got


def test_the_earliest_error_in_the_ring_is_the_cause():
    """With more than one ERROR line in the tail, the first one is quoted.

    Anything after it is that cause's fallout — a cleanup that cannot find the
    directory the first error had already abandoned, an abort summary counting
    the first error. This is the same rule ``AnalyzeTab._poll_log`` applies to
    the queue events it drains, where the first ``("error", …)`` of a run is
    latched and later ones are dropped. The two have to agree: the GUI reports
    the first and this text *is* what the GUI reports.
    """
    from services.pipeline_runner import exit_message

    got = exit_message(1, [
        "2026-08-08 19:10:32  ERROR     StarDist failed on well A01: out of memory",
        "2026-08-08 19:10:33  ERROR     could not remove scratch dir (already gone)",
        "2026-08-08 19:10:33  INFO      Exiting.",
    ])
    assert got.startswith("StarDist failed on well A01"), got
    assert "already gone" not in got


def test_allow_low_disk_reaches_the_argv_only_when_asked():
    from services.pipeline_service import build_pipeline_args

    base = dict(
        nuclear_token="NIR", fluor_tokens=["GFP"], csv_prefix="m",
        filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_", segmentation_method="stardist_nuclei",
        min_nucleus_area_px=50, smfish_tokens=[], shape_features=[],
        tf_threads="0", workers="0", flatfield="none", background_well="",
        qc_remove_debris=False, qc_min_area=0.0, qc_max_area=0.0,
        qc_min_solidity=0.0,
    )
    P, IN, OUT = Path("/pipe.py"), Path("/in"), Path("/out")
    assert "--allow_low_disk" not in build_pipeline_args(P, IN, OUT, dict(base))
    assert "--allow_low_disk" in build_pipeline_args(
        P, IN, OUT, dict(base, allow_low_disk=True))
