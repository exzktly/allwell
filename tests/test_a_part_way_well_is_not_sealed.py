"""A well's output is sealed as complete only when it covers the whole well.

The review that found it: a batched remote resume adopted any ``<well>_out.zip``
it found on the remote as a finished well. A well that was suspended part way —
by the user, or by the mid-batch watchdog once the app had died — writes its CSV
and out.zip from the groups it finished and prints "— done." like a whole one,
so the resume downloaded the partial output, journalled the well as fetched,
and its missing FOV-groups were never computed. The resume now adopts a well
only on its completion seal (``services/remote_batch_resume.py``); these pin
that the pipeline writes that seal for a whole well and never for a part-way
one, and that a part-way well keeps the checkpoints its resume continues from.

``main`` runs for real, with the well pool run serially in this process and the
segmenter stubbed (``stubbed_segmenter``) — the same seams
``test_a_resumed_run_keeps_its_field_quality`` uses. The resume is run without
``--incremental``, because that is how a remote resume runs the pipeline: the
flag is a client-side concern there (``RemoteRunner._build_remote_argv``).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

import pipeline.suspend as _suspend
import process_microscopy as pm
from pipeline.incremental_keys import read_done_fov_keys
from pipeline.run_report import complete_seal_path
from tests.test_a_resumed_run_keeps_its_field_quality import (  # noqa: F401 — a fixture, used by name
    SCHEMA, _REAL_GROUP, _plate, stubbed_segmenter,
)

pytestmark = pytest.mark.usefixtures("stubbed_segmenter")


def _main(monkeypatch, plate, *, suspend_after: int | None = None, remote: bool = True) -> None:
    """Run the pipeline over *plate*; with *suspend_after*, a suspend lands once that many groups have run.

    *remote* publishes a suspend request file as ``--suspend_file`` does (``pipeline.suspend.watch_request_file``),
    which only the remote launch paths pass; the request itself is the stubbed ``is_suspended``.
    """
    if remote:
        monkeypatch.setenv(_suspend.REQUEST_FILE_ENV, str(plate / "never-written-request"))
    else:
        monkeypatch.delenv(_suspend.REQUEST_FILE_ENV, raising=False)
    ran: list[str] = []

    def _group(nuclear_path, fluor_paths, **kwargs):
        out = _REAL_GROUP(nuclear_path, fluor_paths, **kwargs)
        # The stub segmenter writes no images; one per group stands in for its overlay, so the well has an out.zip.
        (Path(kwargs["output_dir"]) / f"{Path(nuclear_path).stem}_overlay.png").write_bytes(b"\0" * 16)
        ran.append(Path(nuclear_path).name)
        return out

    def _serial(*, well_zips, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
                schema, sep, incremental, incremental_prior_dir, settings_key, force, **_ignored):
        for label, zip_path in well_zips:
            pm.process_well_zip_task(label, zip_path, output_dir, shared_kwargs_template, nuclear_token,
                                     fluor_tokens, csv_prefix, schema, sep, incremental, incremental_prior_dir,
                                     settings_key, force)
        return []

    monkeypatch.setattr(pm, "process_image_group", _group)
    monkeypatch.setattr(pm, "process_well_zips", _serial)
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


def _csv(plate) -> Path:
    return next((plate / "out").glob("*_C01.csv*"))


def _checkpoints(plate) -> list[Path]:
    return sorted((plate / "scratch").glob("_tmp_images_*_C01/.fov_*.done"))


def test_a_whole_well_is_sealed(tmp_path, monkeypatch):
    plate = _plate(tmp_path)
    _main(monkeypatch, plate)
    assert (plate / "out" / "C01_out.zip").is_file()
    assert complete_seal_path(plate / "out", "C01").is_file(), "a well that covered its input was not sealed"
    assert _checkpoints(plate) == [], "a whole well has nothing to resume, so its staging goes"


def test_a_well_suspended_part_way_is_not_sealed_and_keeps_its_checkpoints(tmp_path, monkeypatch, caplog):
    plate = _plate(tmp_path)
    _main(monkeypatch, plate, suspend_after=1)

    assert (plate / "out" / "C01_out.zip").is_file(), "the fixture must leave the partial out.zip the review saw"
    assert {fov for fov, _tp in read_done_fov_keys(_csv(plate))} == {"F001"}
    assert not complete_seal_path(plate / "out", "C01").exists(), (
        "a well suspended one group into three was sealed as complete, so a resume would adopt it")
    assert len(_checkpoints(plate)) == 1, (
        "the part-way well's checkpoint was deleted, so a remote resume recomputes the group it had finished")

    # The resume, as the remote runs it: no --incremental, same output directory.
    with caplog.at_level(logging.INFO):
        _main(monkeypatch, plate)
    assert "resumed from checkpoint" in caplog.text, "the finished group was computed again"
    assert {fov for fov, _tp in read_done_fov_keys(_csv(plate))} == {"F001", "F002", "F003"}
    assert complete_seal_path(plate / "out", "C01").is_file(), "the resumed, now whole, well was not sealed"


def test_a_local_suspend_does_not_hold_the_staging(tmp_path, monkeypatch):
    """Locally the resume is ``--incremental`` and reads the finished groups from the CSV, so checkpoints kept
    there would be scratch disk held for nothing. The part-way well is still unsealed."""
    plate = _plate(tmp_path)
    _main(monkeypatch, plate, suspend_after=1, remote=False)
    assert _checkpoints(plate) == []
    assert not complete_seal_path(plate / "out", "C01").exists()


def test_rewriting_a_well_withdraws_its_seal_first(tmp_path, monkeypatch):
    """A well run again into a directory where it was once whole: if the new run dies between its record and its
    archive, the older archive must not stand there under a seal the new run never earned."""
    from pipeline.run_report import write_well_report

    out = tmp_path / "out"
    complete_seal_path(out, "C01").parent.mkdir(parents=True)
    complete_seal_path(out, "C01").write_text("{}")
    write_well_report(out, "C01", groups_total=3, groups_covered=1)
    assert not complete_seal_path(out, "C01").exists()
