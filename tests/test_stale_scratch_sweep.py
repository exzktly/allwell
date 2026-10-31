"""The stale-scratch sweep: what a run removes from earlier runs' working dirs.

Staging lives under the scratch base (``pipeline.io.scratch_base_for``), and
runs from before that default moved left it under ``output_dir``; the sweep
covers both, keeps a directory that still holds FOV checkpoints, and does not
sweep one place twice when the two coincide.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


import pipeline.io as pio


def _abandon(*folders):
    """Make *folders* look untouched for longer than the sweep's idle limit: a
    folder that is not this plate's is removed only then, because it may be a
    live run's (``pipeline.io.FOREIGN_SCRATCH_IDLE_S``)."""
    old = time.time() - pio.FOREIGN_SCRATCH_IDLE_S - 60
    for folder in folders:
        os.utime(folder, (old, old))


def test_stale_sweep_covers_both_scratch_and_output_dir(monkeypatch, tmp_path):
    """Runs from before the default moved left staging under output_dir; the
    sweep must still reclaim it once it is abandoned."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out = tmp_path / "out"
    out.mkdir()

    (scratch / "_tmp_images_A01").mkdir()
    (out / "_tmp_images_B02").mkdir()      # legacy location
    (out / "_tmp_extract_B02").mkdir()
    _abandon(scratch / "_tmp_images_A01", out / "_tmp_images_B02", out / "_tmp_extract_B02")

    monkeypatch.setattr(pio, "scratch_base_for", lambda _out: scratch)
    pio._sweep_stale_scratch_dirs_all(out, extra_extract=True)

    assert not (scratch / "_tmp_images_A01").exists()
    assert not (out / "_tmp_images_B02").exists()
    assert not (out / "_tmp_extract_B02").exists()


def test_stale_sweep_keeps_checkpointed_dirs(monkeypatch, tmp_path):
    """A staging dir holding FOV checkpoints is resumable — never swept."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out = tmp_path / "out"
    out.mkdir()

    keep = scratch / "_tmp_images_A01"
    keep.mkdir()
    (keep / ".fov_1.done").touch()

    monkeypatch.setattr(pio, "scratch_base_for", lambda _out: scratch)
    pio._sweep_stale_scratch_dirs_all(out, extra_extract=False)

    assert keep.exists()


def test_stale_sweep_does_not_double_sweep_when_equal(monkeypatch, tmp_path):
    """When scratch == output_dir (the last-resort fallback) the output dir must
    not be swept twice."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "_tmp_images_A01").mkdir()
    _abandon(out / "_tmp_images_A01")

    calls = []
    real = pio._sweep_stale_scratch_dirs

    def _spy(root, **kwargs):
        calls.append(Path(root))
        return real(root, **kwargs)

    monkeypatch.setattr(pio, "scratch_base_for", lambda o: o)
    monkeypatch.setattr(pio, "_sweep_stale_scratch_dirs", _spy)
    pio._sweep_stale_scratch_dirs_all(out, extra_extract=False)

    assert calls == [out]
    assert not (out / "_tmp_images_A01").exists()
