"""Tests for the v5.2.0 per-FOV checkpoint primitives in
``process_microscopy.py``.

The checkpoint helpers are pure file-IO: they don't require StarDist,
TensorFlow, or any of the heavyweight pipeline dependencies. We exercise
them directly here so the determinism contract ("records loaded from a
checkpoint produce the same CSV as records computed live") is verified
without ever calling ``process_image_group``.

Determinism of the StarDist segmentation itself is out of scope for unit
tests — it lives in the existing integration test surface (run-once vs.
run-with-mid-well-kill) called out in the v5.2.0 plan and run manually
against a synthetic dataset.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# process_microscopy imports tifffile + numpy at module top. Skip the
# whole file when those aren't available in the lean test runner.
pytest.importorskip("numpy")
pytest.importorskip("tifffile")

from process_microscopy import (  # noqa: E402
    _FOV_CHECKPOINT_SCHEMA,
    _checkpoint_paths,
    _fov_stem,
    _json_safe,
    _load_fov_checkpoint,
    _prune_scratch_files,
    _save_fov_checkpoint,
)


# ── _json_safe ──────────────────────────────────────────────────────────────


def test_json_safe_passes_python_scalars_through():
    """Records that contain only plain Python types must survive a
    round-trip unchanged."""
    records = [
        {"well": "A01", "fov": "F001", "area": 137, "mean_intensity": 1.234},
        {"well": "A01", "fov": "F001", "area": 92, "mean_intensity": 5.678},
    ]
    cleaned = _json_safe(records)
    assert cleaned == records
    # And actually JSON-able.
    assert json.loads(json.dumps(cleaned)) == records


def test_json_safe_unwraps_numpy_scalars():
    import numpy as np

    records = [
        {"area": np.int64(137), "mean_intensity": np.float32(1.5)},
        {"area": np.int32(42), "max_intensity": np.float64(99.9)},
    ]
    cleaned = _json_safe(records)
    # All values must be plain Python builtins.
    for r in cleaned:
        for v in r.values():
            assert not hasattr(v, "dtype"), \
                f"numpy scalar leaked through _json_safe: {v!r}"
    # Round-trip through JSON succeeds.
    decoded = json.loads(json.dumps(cleaned))
    assert decoded[0]["area"] == 137
    assert decoded[1]["max_intensity"] == pytest.approx(99.9)


def test_json_safe_handles_none_values():
    records = [{"well": "A01", "fov": None, "area": 137}]
    cleaned = _json_safe(records)
    assert cleaned[0]["fov"] is None


# ── _fov_stem / _checkpoint_paths ───────────────────────────────────────────


def test_fov_stem_is_path_stem():
    assert _fov_stem(Path("/tmp/F001_NIR.tif")) == "F001_NIR"
    assert _fov_stem(Path("Exp_NIR_A01_F002_02d04h.tif")) == "Exp_NIR_A01_F002_02d04h"


def test_checkpoint_paths_distinguishes_records_and_marker(tmp_path):
    records_p, marker_p = _checkpoint_paths(tmp_path, "F001")
    assert records_p.name.startswith(".fov_F001.")
    assert records_p.name.endswith(".records.json")
    assert marker_p.name == ".fov_F001.done"
    # The two are siblings.
    assert records_p.parent == marker_p.parent == tmp_path


# ── save → load round trip ──────────────────────────────────────────────────


def test_save_then_load_round_trip(tmp_path):
    records = [
        {"well": "A01", "fov": "F001", "area": 137, "mean_intensity": 1.5},
        {"well": "A01", "fov": "F001", "area": 92, "mean_intensity": 2.5},
    ]
    _save_fov_checkpoint(tmp_path, "F001", records)
    loaded = _load_fov_checkpoint(tmp_path, "F001")
    assert loaded == records


def test_save_writes_both_marker_and_records(tmp_path):
    _save_fov_checkpoint(tmp_path, "F001", [{"x": 1}])
    records_p, marker_p = _checkpoint_paths(tmp_path, "F001")
    assert records_p.is_file()
    assert marker_p.is_file()


def test_save_uses_atomic_rename(tmp_path):
    """Verify the .tmp file is gone after save (i.e. it was renamed,
    not left behind)."""
    _save_fov_checkpoint(tmp_path, "F001", [{"x": 1}])
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


# ── _load_fov_checkpoint failure modes ──────────────────────────────────────


def test_load_returns_none_when_marker_missing(tmp_path):
    """records.json present but no .done marker — the previous run
    crashed AFTER writing records but BEFORE creating the marker, so
    the records may be incomplete. Treat as no checkpoint."""
    records_p, _ = _checkpoint_paths(tmp_path, "F001")
    records_p.write_text(json.dumps({"schema": 1, "records": []}))
    assert _load_fov_checkpoint(tmp_path, "F001") is None


def test_load_returns_none_when_records_missing(tmp_path):
    """Marker present but records.json missing — fail-safe to reprocess."""
    _, marker_p = _checkpoint_paths(tmp_path, "F001")
    marker_p.touch()
    assert _load_fov_checkpoint(tmp_path, "F001") is None


def test_load_returns_none_on_corrupt_json(tmp_path):
    records_p, marker_p = _checkpoint_paths(tmp_path, "F001")
    records_p.write_text("{ corrupt json")
    marker_p.touch()
    assert _load_fov_checkpoint(tmp_path, "F001") is None


def test_load_returns_none_on_schema_mismatch(tmp_path):
    """A future pipeline reading an old checkpoint must reprocess
    rather than load stale-format records into the CSV."""
    records_p, marker_p = _checkpoint_paths(tmp_path, "F001")
    payload = {"schema": _FOV_CHECKPOINT_SCHEMA + 99, "records": [{"x": 1}]}
    records_p.write_text(json.dumps(payload))
    marker_p.touch()
    assert _load_fov_checkpoint(tmp_path, "F001") is None


def test_load_returns_none_on_wrong_top_level_type(tmp_path):
    records_p, marker_p = _checkpoint_paths(tmp_path, "F001")
    records_p.write_text(json.dumps(["not", "a", "dict"]))
    marker_p.touch()
    assert _load_fov_checkpoint(tmp_path, "F001") is None


# ── _prune_scratch_files ────────────────────────────────────────────────────


def test_prune_removes_scratch_files(tmp_path):
    # Simulate a worker killed mid-_safe_imwrite leaving scratch files.
    (tmp_path / "__aw_tmp_F001_NIR.pid12345_overlay.png").write_bytes(b"\0")
    (tmp_path / "__aw_tmp_F001_NIR.pid12345_labels.tif").write_bytes(b"\0")
    # Plus a legitimate completed output.
    (tmp_path / "F001_NIR_overlay.png").write_bytes(b"\1")
    _prune_scratch_files(tmp_path)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["F001_NIR_overlay.png"]


def test_prune_keeps_checkpoint_markers(tmp_path):
    (tmp_path / ".fov_F001.done").touch()
    (tmp_path / ".fov_F001.records.json").write_text("{}")
    (tmp_path / "__aw_tmp_F002.pid_x.tif").write_bytes(b"\0")
    _prune_scratch_files(tmp_path)
    assert (tmp_path / ".fov_F001.done").exists()
    assert (tmp_path / ".fov_F001.records.json").exists()
    assert not (tmp_path / "__aw_tmp_F002.pid_x.tif").exists()


def test_prune_on_nonexistent_dir_is_silent(tmp_path):
    # Should not raise.
    _prune_scratch_files(tmp_path / "nope")


# ── Resumption semantics ────────────────────────────────────────────────────


def test_simulated_resumption_skips_completed_fovs(tmp_path):
    """End-to-end of the checkpoint primitive flow: process FOV 1,
    persist; pretend the worker died; on restart, FOV 1's records load
    cleanly from disk."""
    records_f1 = [
        {"well": "A01", "fov": "F001", "nucleus": i, "area": 100 + i}
        for i in range(5)
    ]
    records_f2 = [
        {"well": "A01", "fov": "F002", "nucleus": i, "area": 200 + i}
        for i in range(3)
    ]
    _save_fov_checkpoint(tmp_path, "F001", records_f1)
    # F002 was killed mid-processing — no marker yet.

    # On "restart" the worker scans markers and finds only F001.
    done = list(tmp_path.glob(".fov_*.done"))
    assert {p.stem for p in done} == {".fov_F001"}

    # And the cached records load cleanly.
    loaded = _load_fov_checkpoint(tmp_path, "F001")
    assert loaded == records_f1
    # F002 has no checkpoint — caller must reprocess.
    assert _load_fov_checkpoint(tmp_path, "F002") is None


def test_full_well_records_assembly_after_resume(tmp_path):
    """The union of (loaded checkpoints + freshly processed FOVs) must
    equal the records list a fresh-from-zero run would have produced.
    This is the determinism guarantee from the plan."""
    f1 = [{"well": "A01", "fov": "F001", "nucleus": 1, "area": 100}]
    f2 = [{"well": "A01", "fov": "F002", "nucleus": 1, "area": 200}]
    f3 = [{"well": "A01", "fov": "F003", "nucleus": 1, "area": 300}]

    # Run 1: F001 + F002 complete, F003 killed.
    _save_fov_checkpoint(tmp_path, "F001", f1)
    _save_fov_checkpoint(tmp_path, "F002", f2)

    # Run 2: load F001 + F002 from checkpoints, process F003 live.
    assembled: list[dict] = []
    for fov_id, fresh in (("F001", None), ("F002", None), ("F003", f3)):
        cached = _load_fov_checkpoint(tmp_path, fov_id)
        if cached is not None:
            assembled.extend(cached)
        else:
            assembled.extend(fresh)  # type: ignore[arg-type]
            _save_fov_checkpoint(tmp_path, fov_id, fresh)  # type: ignore[arg-type]

    # The assembled records match a hypothetical "ran all three FOVs
    # from scratch" outcome.
    expected = f1 + f2 + f3
    assert assembled == expected
