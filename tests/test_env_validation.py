"""Audit X12 — startup validation of the ``ALLWELL_*`` overrides.

``validate_env`` is pure: given an env mapping it returns
``(warnings, active)`` without touching the real environment or falling
back. A mistyped scratch/remotes path must produce a visible warning; a
well-formed override must produce an "active" line and no warning.
"""
from __future__ import annotations

from services.env import validate_env


def test_no_overrides_is_silent():
    warnings, active = validate_env({})
    assert warnings == []
    assert active == []


def test_valid_scratch_dir_is_active_without_warning(tmp_path):
    good = tmp_path / "scratch"
    good.mkdir()
    warnings, active = validate_env({"ALLWELL_SCRATCH_DIR": str(good)})
    assert warnings == []
    assert any("ALLWELL_SCRATCH_DIR" in a for a in active)


def test_scratch_dir_pointing_at_a_file_warns(tmp_path):
    f = tmp_path / "not_a_dir"
    f.write_text("x")
    warnings, active = validate_env({"ALLWELL_SCRATCH_DIR": str(f)})
    assert any("ALLWELL_SCRATCH_DIR" in w for w in warnings)
    # Still reported as an active override so the log shows what was requested.
    assert any("ALLWELL_SCRATCH_DIR" in a for a in active)


def test_missing_remotes_file_warns(tmp_path):
    missing = tmp_path / "remotes.toml"
    warnings, _ = validate_env({"ALLWELL_REMOTES": str(missing)})
    assert any("ALLWELL_REMOTES" in w for w in warnings)


def test_existing_remotes_file_is_active_without_warning(tmp_path):
    f = tmp_path / "remotes.toml"
    f.write_text("")
    warnings, active = validate_env({"ALLWELL_REMOTES": str(f)})
    assert warnings == []
    assert any("ALLWELL_REMOTES" in a for a in active)


def test_remotes_dir_warns(tmp_path):
    d = tmp_path / "remotes_as_dir"
    d.mkdir()
    warnings, _ = validate_env({"ALLWELL_REMOTES": str(d)})
    assert any("ALLWELL_REMOTES" in w for w in warnings)


def test_seg_debug_flag_is_active_only():
    warnings, active = validate_env({"ALLWELL_SEG_DEBUG": "1"})
    assert warnings == []
    assert any("ALLWELL_SEG_DEBUG" in a for a in active)


def test_blank_override_is_ignored():
    # A set-but-empty var must not be treated as a (bad) path.
    warnings, active = validate_env({"ALLWELL_SCRATCH_DIR": "  ",
                                     "ALLWELL_REMOTES": ""})
    assert warnings == []
    assert active == []
