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
    (out / "A01_out.zip").write_bytes(b"x" * 4096)
    monkeypatch.setattr(cap, "free_bytes", lambda _p: 1)

    warnings: list[str] = []

    class _Log:
        def warning(self, fmt, *args):
            warnings.append(fmt % args)

    monitor = OutputCapacityMonitor(out, total_wells=8, log=_Log())
    for _ in range(4):
        monitor.well_done()
    assert len(warnings) == 1, "one warning, not one per remaining well"
    assert "will not hold the rest of this plate" in warnings[0]

    quiet = OutputCapacityMonitor(out, total_wells=1, log=_Log())
    quiet.well_done()
    assert len(warnings) == 1, "the last well has no remainder to project"


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
