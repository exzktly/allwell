"""Qt-free tests for services/run_spec.py.

RunSpec is the job queue's persisted description of an Analyze run, so its
JSON round-trip and its remote-resolution failure mode are the two things
worth pinning here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.run_spec import RunSpec, RunSpecError


def _full_spec(raw: Path) -> RunSpec:
    return RunSpec(
        opts={
            "raw": raw,
            "force": False,
            "incremental": True,
            "csv_prefix": "measurements",
            "extra_channels": ["gfp", "dapi"],
            "threshold": 0.5,
        },
        remote_name="hpc1",
        cleanup_remote="all",
        dry_run_cleanup=True,
        output_filter="qc-only",
        selective_channels=False,
    )


def test_json_round_trip_is_stable(tmp_path):
    spec = _full_spec(tmp_path / "plate7")
    encoded = spec.to_json()
    # Must survive an actual json.dumps/loads cycle, not just be dict-equal
    # in memory - that's what catches a future non-JSON-native opts value.
    reloaded = json.loads(json.dumps(encoded))
    assert reloaded == encoded

    restored = RunSpec.from_json(reloaded)
    assert restored.opts["raw"] == tmp_path / "plate7"
    assert isinstance(restored.opts["raw"], Path)
    assert restored.remote_name == "hpc1"
    assert restored.cleanup_remote == "all"
    assert restored.dry_run_cleanup is True
    assert restored.output_filter == "qc-only"
    assert restored.selective_channels is False
    assert restored.opts["extra_channels"] == ["gfp", "dapi"]


def test_round_trip_preserves_non_path_opts(tmp_path):
    spec = _full_spec(tmp_path / "plate7")
    restored = RunSpec.from_json(json.loads(json.dumps(spec.to_json())))
    assert restored.opts["force"] is False
    assert restored.opts["incremental"] is True
    assert restored.opts["csv_prefix"] == "measurements"
    assert restored.opts["threshold"] == 0.5


def test_from_json_defaults_missing_fields():
    restored = RunSpec.from_json({})
    assert restored.opts == {}
    assert restored.remote_name is None
    assert restored.cleanup_remote == "none"
    assert restored.dry_run_cleanup is False
    assert restored.output_filter == "full"
    assert restored.selective_channels is True


def test_local_run_resolves_remote_to_none(tmp_path):
    spec = RunSpec(opts={"raw": tmp_path})
    assert spec.resolve_remote() is None


def test_deleted_remote_raises_run_spec_error(tmp_path, monkeypatch):
    # Point at an existing-but-empty remotes.toml, i.e. a remote that used
    # to exist and no longer does - not a missing-file case.
    remotes_file = tmp_path / "remotes.toml"
    remotes_file.write_text("")
    monkeypatch.setenv("ALLWELL_REMOTES", str(remotes_file))

    spec = RunSpec(opts={"raw": tmp_path}, remote_name="gone")
    with pytest.raises(RunSpecError, match="gone"):
        spec.resolve_remote()


def test_runner_opts_rewraps_string_raw_as_path(tmp_path):
    spec = RunSpec.from_json({"opts": {"raw": str(tmp_path)}})
    opts = spec.runner_opts()
    assert isinstance(opts["raw"], Path)
    assert opts["raw"] == tmp_path


def test_label_uses_raw_folder_name(tmp_path):
    spec = RunSpec(opts={"raw": tmp_path / "myplate"})
    assert spec.label() == "Analyze — myplate"


def test_label_handles_missing_raw():
    spec = RunSpec(opts={})
    assert spec.label() == "Analyze — (no input)"
