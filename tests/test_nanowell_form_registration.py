"""The Analyze form's *Reuse registration* field: an earlier nanowell run's output folder whose tile registration the
run reuses (``--nanowell_registration_from``), driven through the real AnalyzeTab — the Browse button, the argv, the
run validation, a sidecar restoring it — and what becomes of it on a remote host."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_form import _argv, _flag, _plate, _use_nanowell_schema, refused, tab  # noqa: E402,F401


def _earlier_run(tmp_path) -> Path:
    out = tmp_path / "earlier_out"
    (out / "nanowells").mkdir(parents=True)
    return out


def test_browse_fills_the_field_and_the_folder_reaches_the_argv(tab, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QFileDialog

    earlier = _earlier_run(tmp_path)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(earlier)))
    tab._nanowell_cb.setChecked(True)
    assert "--nanowell_registration_from" not in _argv(tab)           # blank: only the output folder's own
    tab._nanowell_registration_btn.click()
    assert tab._nanowell_registration_edit.text() == str(earlier)
    assert _flag(_argv(tab), "--nanowell_registration_from") == str(earlier)
    tab._nanowell_cb.setChecked(False)
    assert not tab._nanowell_registration_edit.isEnabled() and not tab._nanowell_registration_btn.isEnabled()
    assert "--nanowell_registration_from" not in _argv(tab)           # the mode off sends none of the section


def test_a_folder_that_is_not_a_nanowell_run_is_refused_and_one_that_is_lets_the_run_go(  # noqa: F811
        tab, refused, tmp_path):  # noqa: F811
    root, _ = _plate(tmp_path)
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_registration_edit.setText(str(tmp_path / "nowhere"))
    assert tab._validate_run_request() is None
    assert f"Reuse registration from: {tmp_path / 'nowhere'} is not a folder." in refused[-1][1]
    (tmp_path / "plain").mkdir()
    tab._nanowell_registration_edit.setText(str(tmp_path / "plain"))
    assert tab._validate_run_request() is None
    assert "is not a nanowell run's output folder" in refused[-1][1]
    tab._nanowell_registration_edit.setText(str(_earlier_run(tmp_path)))
    assert tab._validate_run_request() is not None


def test_a_sidecar_restores_the_folder_and_a_dataset_without_one_clears_it(tab):  # noqa: F811
    tab._apply_nanowell_settings({"nanowell_format": "100um", "nanowell_registration_from": "/data/run1/out"})
    assert tab._collect_run_spec().opts["nanowell_registration_from"] == "/data/run1/out"
    tab._apply_nanowell_settings({"nanowell_format": "100um"})
    assert tab._collect_run_spec().opts["nanowell_registration_from"] == ""


def test_on_a_remote_host_the_run_goes_and_the_log_says_the_host_registers_every_tile(  # noqa: F811
        tab, refused, tmp_path):  # noqa: F811
    root, _ = _plate(tmp_path)
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_registration_edit.setText(str(_earlier_run(tmp_path)))
    logged = []
    tab._log_line = lambda text, tag="INFO": logged.append((text, tag))
    tab._remote_cb.addItem("bench-box", userData="bench-box")
    tab._remote_cb.setCurrentIndex(tab._remote_cb.findData("bench-box"))
    assert tab._validate_run_request() is not None
    assert ("Nanowells: Reuse registration names a folder on this computer, which a remote host cannot read: the "
            "host registers every tile itself (the positions are the same).", "WARNING") in logged


def test_the_remote_argv_drops_the_folder_and_the_run_address_ignores_it():
    from services.remote_config import RemoteHost
    from services.remote_run_id import pipeline_argv_digest
    from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner

    local = ["/usr/bin/python", "/local/process_microscopy.py", "--input_dir", "/local/in", "--output_dir",
             "/local/out", "--nanowell_format", "100um", "--nanowell_registration_from", "/local/run1/out",
             "--nanowell_cell_diameter_um", "11.0"]
    remote = RemoteRunner(RemoteHost(name="t", ssh_alias="t")).\
        _build_remote_argv(local, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP)
    assert "--nanowell_registration_from" not in remote and "/local/run1/out" not in remote
    assert remote[remote.index("--nanowell_cell_diameter_um") + 1] == "11.0"    # the flag after it survives
    without = [a for a in local if a not in ("--nanowell_registration_from", "/local/run1/out")]
    assert pipeline_argv_digest(local) == pipeline_argv_digest(without)
