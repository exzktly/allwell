"""Tests for pipeline.io.scratch_base_for — the redirectable scratch base.

Per-well working files (zip extraction + intermediate images + FOV checkpoints)
live under this directory. Resolution order is ``$ALLWELL_SCRATCH_DIR`` (set by
the Analyze tab's "Scratch disk" field / ``--scratch_dir``), then the
``~/.allwell/scratch`` default, then the output dir as a last resort.

The default deliberately is NOT the output dir: that put the whole run's bulk
I/O on whatever volume the user chose for results, and on a network mount the
resulting load drops the session — which macOS's SMB client reports as
``OSError: [Errno 9] Bad file descriptor`` on every handle opened before the
reconnect.
"""

from __future__ import annotations

from pathlib import Path

import pipeline.io as pio
from pipeline.io import DEFAULT_SCRATCH_SUBDIR, scratch_base_for


def test_unset_returns_home_scratch(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLWELL_SCRATCH_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    out = tmp_path / "out"
    got = scratch_base_for(out)
    assert got == tmp_path / "home" / DEFAULT_SCRATCH_SUBDIR
    assert got.is_dir()  # created on demand


def test_default_is_not_the_output_dir(monkeypatch, tmp_path):
    """The regression guard: bulk I/O must not default onto the output volume."""
    monkeypatch.delenv("ALLWELL_SCRATCH_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    out = tmp_path / "out"
    out.mkdir()
    assert scratch_base_for(out) != out


def test_default_is_nested_not_bare_home(monkeypatch, tmp_path):
    """Staging dirs are swept with a destructive glob, so the base must be a
    dedicated subdirectory rather than the home directory itself."""
    monkeypatch.delenv("ALLWELL_SCRATCH_DIR", raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert scratch_base_for(tmp_path / "out") != home


def test_blank_env_falls_through_to_home(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", "   ")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    got = scratch_base_for(tmp_path / "out")
    assert got == tmp_path / "home" / DEFAULT_SCRATCH_SUBDIR


def test_env_override_is_used_and_created(monkeypatch, tmp_path):
    scratch = tmp_path / "scratch" / "Temp"  # parents don't exist yet
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(scratch))
    out = tmp_path / "out"
    got = scratch_base_for(out)
    assert got == scratch
    assert got.is_dir()  # created on demand


def test_uncreatable_override_falls_back_to_home(monkeypatch, tmp_path):
    # A regular file where a directory is expected → mkdir raises → fall through.
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(blocker / "nope"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    got = scratch_base_for(tmp_path / "out")
    assert got == tmp_path / "home" / DEFAULT_SCRATCH_SUBDIR


def test_unusable_home_falls_back_to_output_dir(monkeypatch, tmp_path):
    """Last resort: an unwritable home must not fail the run."""
    monkeypatch.delenv("ALLWELL_SCRATCH_DIR", raising=False)
    monkeypatch.setattr(pio, "default_scratch_base", lambda: None)
    out = tmp_path / "out"
    assert scratch_base_for(out) == out
