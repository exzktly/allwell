"""A plain re-run runs a well left part way again; it does not skip it as complete (tracker B1).

The report: the completion seal was read only by the app. On Resume, a well the earlier attempt left part way — a
Stop's or a suspend's SIGTERM wind-down, or the mid-batch watchdog — was re-sent into an output directory still
holding its unsealed ``out.zip``, CSV and settings marker, and the host's own skip
(``output_exists_for_well and well_settings_allow_reuse``) skipped it: ``SKIP well … output already exists``. The
partial copy was then fetched as the well's result and the run reported success; a field-mode well stayed wrong on
every later resume until Force.

These run ``process_microscopy.main`` for real — ``process_well_zips`` and the real ``_run_well_worker_pool``, whose
skip is the subject — with only the process pool swapped for one thread in this process (a spawned worker would
not carry the segmenter stub) and the segmenter stubbed (``stubbed_segmenter``). The re-run passes no
``--incremental``, as a remote resume's host is run (``RemoteRunner._build_remote_argv`` drops it).
"""

from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import pipeline.suspend as _suspend
import process_microscopy as pm
from pipeline.incremental_keys import read_done_fov_keys
from pipeline.run_report import COMPLETE_DIRNAME, complete_seal_path, withdrawn_note_path
from tests.test_a_resumed_run_keeps_its_field_quality import (  # noqa: F401 — a fixture, used by name
    SCHEMA, _REAL_GROUP, _plate, stubbed_segmenter,
)

pytestmark = pytest.mark.usefixtures("stubbed_segmenter")


def _main(monkeypatch, plate, *, suspend_after: int | None = None) -> list[str]:
    """Run the pipeline over *plate* through the real pool; return the FOV-groups this invocation segmented."""
    monkeypatch.setenv(_suspend.REQUEST_FILE_ENV, str(plate / "never-written-request"))
    ran: list[str] = []

    def _group(nuclear_path, fluor_paths, **kwargs):
        out = _REAL_GROUP(nuclear_path, fluor_paths, **kwargs)
        (Path(kwargs["output_dir"]) / f"{Path(nuclear_path).stem}_overlay.png").write_bytes(b"\0" * 16)
        ran.append(Path(nuclear_path).name)
        return out

    monkeypatch.setattr(pm, "process_image_group", _group)
    monkeypatch.setattr(pm, "_well_pool", lambda *a, **k: ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(pm, "_ensure_stardist_runtime_deps", lambda: None)
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    monkeypatch.setattr(_suspend, "is_suspended",
                        lambda: suspend_after is not None and len(ran) >= suspend_after)
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(plate / "out"),
        "--scratch_dir", str(plate / "scratch"), "--nuclear_token", "DAPI", "--fluor_tokens", "GFP",
        "--filename_schema", ":".join(SCHEMA), "--workers", "1", "--background", "none",
        "--flatfield", "none", "--no_auto_threshold", "--no_run_check"])
    try:
        pm.main()
    except SystemExit:
        pass                     # a suspended run may exit non-zero; what it left on disk is the subject
    return ran


def _done(plate) -> set[str]:
    return {fov for fov, _tp in read_done_fov_keys(next((plate / "out").glob("*_C01.csv*")))}


def test_a_well_left_part_way_is_run_again_not_skipped(tmp_path, monkeypatch, caplog):
    plate = _plate(tmp_path)
    _main(monkeypatch, plate, suspend_after=1)
    assert _done(plate) == {"F001"} and (plate / "out" / "C01_out.zip").is_file(), (
        "the fixture must leave what the review saw: a part-way well's CSV and out.zip")
    assert not complete_seal_path(plate / "out", "C01").exists()

    with caplog.at_level(logging.INFO):
        _main(monkeypatch, plate)
    assert "SKIP well C01" not in caplog.text, (
        "the host skipped a well left part way as complete, so its partial output would be fetched as the result")
    assert _done(plate) == {"F001", "F002", "F003"}, f"the re-run left the well at {sorted(_done(plate))}"
    assert complete_seal_path(plate / "out", "C01").is_file(), "the well, now whole, was not sealed"
    assert not withdrawn_note_path(plate / "out", "C01").exists(), "the seal did not clear its withdrawal note"


def test_a_well_another_build_finished_is_reused_and_said_so(tmp_path, monkeypatch, caplog):
    """Tracker F7: neither reuse key names the build, so a well a previous version of All-Well finished is reused
    by this one in silence. The seal now records the build, and a reuse across builds is named per well."""
    from version import __version__

    plate = _plate(tmp_path)
    _main(monkeypatch, plate)
    seal = complete_seal_path(plate / "out", "C01")
    assert json.loads(seal.read_text())["build"] == __version__, "the seal does not say which build made the well"
    seal.write_text(json.dumps({"well": "C01", "build": "0.0.1"}))
    with caplog.at_level(logging.INFO):
        ran = _main(monkeypatch, plate)
    assert ran == [], "a whole well was recomputed only because another build sealed it"
    assert f"Well C01 was finished by build 0.0.1, not this build ({__version__})" in caplog.text, caplog.text


def test_a_sealed_well_is_still_skipped(tmp_path, monkeypatch, caplog):
    """The other side of the same predicate: a whole well is not recomputed by a plain re-run."""
    plate = _plate(tmp_path)
    _main(monkeypatch, plate)
    with caplog.at_level(logging.INFO):
        ran = _main(monkeypatch, plate)
    assert ran == [] and "SKIP well C01" in caplog.text, f"a sealed, whole well was segmented again: {ran}"


def test_a_well_whose_rewrite_died_is_run_again(tmp_path, monkeypatch):
    """A whole well rewritten (a Force, a re-run under new settings) by a run that died between withdrawing its seal
    and writing its record: the sidecar still says the *earlier* computation was whole, and the output may be half
    the new one. The withdrawal note is what says a sealing build began rewriting it."""
    from pipeline.run_report import withdraw_seal

    plate = _plate(tmp_path)
    _main(monkeypatch, plate)
    withdraw_seal(plate / "out", "C01")                      # the rewrite began here, and nothing after it ran
    ran = _main(monkeypatch, plate)
    assert len(ran) == 3, f"a well whose rewrite never finished was skipped on its stale record: segmented {ran}"


def _as_an_older_build_left_it(plate, *, record: dict | None) -> None:
    """A whole run, then the seal and its note removed — the output a build without either leaves — with *record*
    as the completeness record its sidecar carries (``None``: none, as a build before the record left)."""
    out = plate / "out"
    complete_seal_path(out, "C01").unlink()
    withdrawn_note_path(out, "C01").unlink(missing_ok=True)
    info = json.loads((out / "pipeline_info.json").read_text())
    if record is None:
        info.pop("run_report", None)
    else:
        info["run_report"] = {"wells": {"C01": record}}
    (out / "pipeline_info.json").write_text(json.dumps(info))
    assert not any((out / COMPLETE_DIRNAME).iterdir())


def test_output_an_earlier_build_finished_is_still_reused(tmp_path, monkeypatch, caplog):
    """No seal and no note: a plate made before the seal existed. Requiring the seal there would recompute every
    plate anyone re-runs; it keeps the reuse it always had, as an unrecorded settings marker does."""
    plate = _plate(tmp_path)
    _main(monkeypatch, plate)
    _as_an_older_build_left_it(plate, record=None)
    with caplog.at_level(logging.INFO):
        ran = _main(monkeypatch, plate)
    assert ran == [], f"output an earlier build finished was recomputed: {ran}"


def test_output_an_earlier_build_left_part_way_is_run_again(tmp_path, monkeypatch):
    """The upgrade case: an attempt by a build that sealed but left no note, wound down part way — its record says
    so. Without the record check the new build would grandfather it like a finished one."""
    plate = _plate(tmp_path)
    _main(monkeypatch, plate)
    _as_an_older_build_left_it(plate, record={"well": "C01", "groups_total": 3, "groups_covered": 1,
                                              "aborted_reason": "suspended"})
    ran = _main(monkeypatch, plate)
    assert len(ran) == 3, f"a well its own record says was left part way was skipped: segmented {ran}"
