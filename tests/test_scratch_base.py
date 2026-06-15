"""Tests for pipeline.io.scratch_base_for — the redirectable scratch base.

Per-well working files (zip extraction + intermediate images) live under this
directory; it honors ``ALLWELL_SCRATCH_DIR`` (set by the Analyze tab's
"Scratch disk" field) and falls back to the output dir.
"""

from __future__ import annotations

from pipeline.io import scratch_base_for


def test_unset_returns_output_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLWELL_SCRATCH_DIR", raising=False)
    out = tmp_path / "out"
    assert scratch_base_for(out) == out


def test_blank_env_returns_output_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", "   ")
    out = tmp_path / "out"
    assert scratch_base_for(out) == out


def test_env_override_is_used_and_created(monkeypatch, tmp_path):
    scratch = tmp_path / "scratch" / "Temp"  # parents don't exist yet
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(scratch))
    out = tmp_path / "out"
    got = scratch_base_for(out)
    assert got == scratch
    assert got.is_dir()  # created on demand


def test_uncreatable_override_falls_back(monkeypatch, tmp_path):
    # A regular file where a directory is expected → mkdir raises → fall back.
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(blocker / "nope"))
    out = tmp_path / "out"
    assert scratch_base_for(out) == out
