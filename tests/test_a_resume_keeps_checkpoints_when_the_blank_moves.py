"""A resumed batched run keeps its wells' part-way checkpoints though the blank's path changed.

Reported on 9.12.9: a restarted remote run logged "Keeping checkpoint dir …", then "Removing staging left by a
previous settings combination", and started a well about 25% done from its first field. The blank (empty-well
reference) is passed as ``--background_dir=<run dir>/bg``, and ``background_dir`` is part of the settings digest
that names a well's staging directory. A batched run's run directory is new on every attempt, so the resume's
digest differed from the attempt it resumed, and ``prune_superseded_staging`` deleted that attempt's checkpoints.

The fix: the remote side passes every earlier attempt's ``bg/`` as ``--background_dir_alias`` (read from the run
journal), and the pipeline accepts a digest computed with any of them. These tests drive that sequence end to end —
journal → the arguments the remote side adds → the real pipeline parser → the real prune and takeover.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from pipeline.cli_args import build_parser
from pipeline.fov_checkpoint import (
    _output_stamp, _settings_stamp, _staging_dir_for, prune_superseded_staging)
from services import run_journal
from services.remote_batch_resume import background_aliases
from util.settings_digest import settings_key_for

KEYS = dict(argv_digest="argv1", source_key="src1", host_alias="cluster")
STAGING = "/home/u/allwell_staging/runs"


def _key(argv: list[str]) -> str:
    """The settings key the pipeline computes for *argv* (process_microscopy: ``settings_key_for(vars(args))``)."""
    args = build_parser().parse_args(["--input_dir", "/r/in", "--output_dir", "/r/out", *argv])
    return settings_key_for(vars(args))


def _record(out: Path, run: str, *, output_run: str) -> None:
    run_journal.record_remote(out, run_id=run, cache_key="k", host_alias=KEYS["host_alias"],
                              remote_input_dir=f"{STAGING}/{run}/in",
                              remote_output_dir=f"{STAGING}/{output_run}/out",
                              argv_digest=KEYS["argv_digest"], source_key=KEYS["source_key"])


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(run_journal, "index_path", lambda: tmp_path / "state" / "idx.json")
    out = tmp_path / "local_out"
    out.mkdir()
    run_journal.open_run(out, mode="remote", input_dir=tmp_path / "in", spec={"opts": {}})
    # Attempt 1 ran in run-1. Attempt 2 (the reported one) ran in run-2 and re-attached to run-1's output.
    _record(out, "run-1", output_run="run-1")
    _record(out, "run-2", output_run="run-1")
    return out


def _proc(local_out: Path, run: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        _journal_keys=KEYS, _legacy_digests=(), _local_output_dir=local_out,
        _runner=types.SimpleNamespace(_run=types.SimpleNamespace(
            remote_output_dir=f"{STAGING}/run-1/out")))


def test_every_earlier_attempt_s_blank_path_is_passed(journal):
    _record(journal, "run-3", output_run="run-1")              # this attempt records itself first
    args = background_aliases(_proc(journal, "run-3"), f"{STAGING}/run-3", True)
    assert set(args) == {f"--background_dir_alias={STAGING}/run-1/bg",
                         f"--background_dir_alias={STAGING}/run-2/bg"}


def test_no_blank_adds_nothing(journal):
    assert background_aliases(_proc(journal, "run-3"), f"{STAGING}/run-3", False) == []


@pytest.mark.parametrize("earlier_run", ["run-1", "run-2"])
def test_the_earlier_attempt_s_checkpoints_are_kept_and_taken_over(journal, tmp_path, monkeypatch, earlier_run):
    """The reported sequence, with the fix: nothing pruned, and the well resumes from its checkpoints."""
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(tmp_path / "scratch"))
    remote_out = tmp_path / "remote_out"                       # the re-attached output dir, as the pipeline sees it
    remote_out.mkdir()
    blank = ["--flatfield", "empty_well", "--background_well", "H12"]
    then = _key([*blank, f"--background_dir={STAGING}/{earlier_run}/bg"])
    old = (tmp_path / "scratch" /
           f"_tmp_images_{_output_stamp(remote_out)}_{_settings_stamp(then)}_A01")
    old.mkdir(parents=True)
    (old / ".fov_F001_T01.done").touch()

    _record(journal, "run-3", output_run="run-1")
    aliases = background_aliases(_proc(journal, "run-3"), f"{STAGING}/run-3", True)
    now = _key([*blank, f"--background_dir={STAGING}/run-3/bg", *aliases])

    assert prune_superseded_staging(remote_out, now) == 0, "the earlier attempt's checkpoints were pruned"
    here = _staging_dir_for(remote_out, "A01", now)
    assert (here / ".fov_F001_T01.done").exists(), "the well did not take over its checkpoints"


def test_without_the_aliases_the_checkpoints_are_pruned(tmp_path, monkeypatch):
    """The control — the 9.12.9 behaviour: the moved blank alone reads as other settings."""
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(tmp_path / "scratch"))
    remote_out = tmp_path / "remote_out"
    remote_out.mkdir()
    blank = ["--flatfield", "empty_well", "--background_well", "H12"]
    then = _key([*blank, f"--background_dir={STAGING}/run-1/bg"])
    old = (tmp_path / "scratch" /
           f"_tmp_images_{_output_stamp(remote_out)}_{_settings_stamp(then)}_A01")
    old.mkdir(parents=True)
    (old / ".fov_F001_T01.done").touch()
    now = _key([*blank, f"--background_dir={STAGING}/run-3/bg"])
    assert prune_superseded_staging(remote_out, now) == 1
    assert not old.exists()


def test_an_alias_never_stands_in_for_a_different_setting():
    """Only the blank's path is widened: a run differing in anything else still reads as other settings."""
    then = _key(["--flatfield", "empty_well", "--background_well", "G12",
                 f"--background_dir={STAGING}/run-1/bg"])
    now = _key(["--flatfield", "empty_well", "--background_well", "H12",
                f"--background_dir={STAGING}/run-3/bg", f"--background_dir_alias={STAGING}/run-1/bg"])
    assert then.split("~")[0] not in now.split("~")
