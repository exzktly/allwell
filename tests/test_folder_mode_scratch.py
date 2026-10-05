"""Folder-mode staging must honour the scratch base, like zip mode does.

Folder mode used to pin ``_tmp_images_<well>`` to ``output_dir`` so its closing
commit could be a same-filesystem ``rename``. That made the Analyze tab's
"Scratch disk" setting a no-op for folder-mode runs and put every intermediate
image on the output volume — the dominant source of I/O behind the "Bad file
descriptor" well failures on a network-mounted output dir.

``_commit_staging_dir`` now handles the cross-filesystem case so the staging
dir can live anywhere.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

import pipeline.io as pio


def test_commit_staging_dir_renames_when_same_filesystem(tmp_path):
    stage = tmp_path / "_tmp_images_A01"
    stage.mkdir()
    (stage / "a_labels.tif").write_bytes(b"x")
    final = tmp_path / "A01"

    pio._commit_staging_dir(stage, final)

    assert not stage.exists()
    assert (final / "a_labels.tif").read_bytes() == b"x"


def test_commit_staging_dir_copies_across_filesystems(monkeypatch, tmp_path):
    stage = tmp_path / "scratch" / "_tmp_images_A01"
    stage.mkdir(parents=True)
    (stage / "a_labels.tif").write_bytes(b"x")
    (stage / "sub").mkdir()
    (stage / "sub" / "b.tif").write_bytes(b"y")
    final = tmp_path / "out" / "A01"
    final.parent.mkdir()

    def _exdev(self, target):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(Path, "rename", _exdev)

    pio._commit_staging_dir(stage, final)

    assert (final / "a_labels.tif").read_bytes() == b"x"
    assert (final / "sub" / "b.tif").read_bytes() == b"y"
    assert not stage.exists()  # staging cleaned up after the copy


def test_commit_staging_dir_propagates_other_oserrors(monkeypatch, tmp_path):
    """Only EXDEV falls back to a copy; anything else is a real failure."""
    stage = tmp_path / "_tmp_images_A01"
    stage.mkdir()
    final = tmp_path / "A01"

    def _eacces(self, target):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(Path, "rename", _eacces)

    with pytest.raises(OSError) as ei:
        pio._commit_staging_dir(stage, final)
    assert ei.value.errno == errno.EACCES


def test_stale_sweep_covers_both_scratch_and_output_dir(monkeypatch, tmp_path):
    """Runs from before the default moved left staging under output_dir; the
    sweep must still reclaim it."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out = tmp_path / "out"
    out.mkdir()

    (scratch / "_tmp_images_A01").mkdir()
    (out / "_tmp_images_B02").mkdir()      # legacy location
    (out / "_tmp_extract_B02").mkdir()

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

    calls = []
    real = pio._sweep_stale_scratch_dirs

    def _spy(root, *, extra_extract):
        calls.append(Path(root))
        return real(root, extra_extract=extra_extract)

    monkeypatch.setattr(pio, "scratch_base_for", lambda o: o)
    monkeypatch.setattr(pio, "_sweep_stale_scratch_dirs", _spy)
    pio._sweep_stale_scratch_dirs_all(out, extra_extract=False)

    assert calls == [out]
    assert not (out / "_tmp_images_A01").exists()
