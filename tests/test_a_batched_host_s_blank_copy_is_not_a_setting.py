"""A plate a batched remote host computed against its copy of the blank is recognised as finished afterwards.

Review tracker H3. A batched run stages one batch at a time, so the empty-well blank is uploaded once to
``<run dir>/bg`` and named to every batch with ``--background_dir``. That option is in the settings key, because a
directory a person names holds another blank — but this one is the app's copy of the input's own blank, at a path
that is new on every attempt. The host therefore recorded each well under a key no later question could produce:
this computer asks with no ``--background_dir`` at all, before staging (``PipelineRunner._finished_here``) and in a
local run. Measured before the fix, on a real three-well plate: the host's record ``ff406195c786be1e`` matched
neither question, a local re-run processed 3 wells and skipped 0, and a single-shot remote re-run found no well
finished — while the same plate computed without the copy was skipped whole.

The host's argv now carries ``--background_dir_carried`` beside the path, and a carried copy contributes what an
unset ``--background_dir`` does. These go through the app's own order: ``RemoteRunner._build_remote_argv`` builds
the argv a batch runs under, the pipeline's ``settings_key_from_argv`` derives what it records, and the question
is then asked by the real ``PipelineRunner._run_pipeline_remote`` and by the local run's own key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_a_remote_rerun_skips_finished_wells import _opts, _plate, _run

pytest.importorskip("PySide6")

#: A01 is a sample well and B02 the plate's blank.
BLANK = dict(background="empty_well", background_well="B02")


def _local_argv(inp: Path, out: Path) -> list[str]:
    from services.pipeline_service import build_pipeline_args

    return build_pipeline_args(Path("process_microscopy.py"), inp, out, _opts(**BLANK))


def _host_flags(inp: Path, out: Path, run_dir: str) -> list[str]:
    """The flags one batch's pipeline runs under on the host, built by the code that builds them."""
    from services.remote_commands import batch_pipeline_argv
    from services.remote_config import RemoteHost
    from services.remote_finished_wells import _flags
    from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner

    runner = object.__new__(RemoteRunner)
    runner.host = RemoteHost(name="h", ssh_alias="h")
    runner.env_selector = ("name", "allwell")
    argv = runner._build_remote_argv(_local_argv(inp, out), f"{run_dir}/in", f"{run_dir}/out", OutputFilter.FULL,
                                     LocalInputMode.ZIP, background_dir=f"{run_dir}/bg")
    return _flags(batch_pipeline_argv(argv))


def _recorded_by_the_host(inp: Path, out: Path, run_dir: str) -> str:
    """What the host's pipeline writes beside each well: the first part of its settings key."""
    from pipeline.cli_args import settings_key_from_argv
    from util.settings_digest import primary_settings_key

    flags = _host_flags(inp, out, run_dir)
    assert f"--background_dir={run_dir}/bg" in flags          # the fixture is the batched case, blank copy and all
    return primary_settings_key(settings_key_from_argv(flags))


def test_a_remote_re_run_sends_nothing_of_a_plate_a_batched_host_computed(tmp_path):
    from pipeline.settings_record import record_well_settings

    inp, out = _plate(tmp_path)
    for well in ("A01", "B02"):
        record_well_settings(out, well, _recorded_by_the_host(inp, out, "/stage/runs/run-1"))
    sent, lines = _run(inp, out, _opts(**BLANK))
    assert sent is None, "wells the batched host finished were uploaded again"
    assert any("1 well(s) already finished here" in ln and "A01" in ln for ln in lines)
    assert any("Nothing to upload" in ln for ln in lines)


def test_a_local_run_of_the_same_settings_reuses_what_the_batched_host_recorded(tmp_path):
    from pipeline.cli_args import settings_key_from_argv
    from pipeline.settings_record import record_well_settings, well_settings_allow_reuse

    inp, out = _plate(tmp_path)
    record_well_settings(out, "A01", _recorded_by_the_host(inp, out, "/stage/runs/run-1"))
    asked = settings_key_from_argv(_local_argv(inp, out)[2:])
    assert well_settings_allow_reuse(out, "A01", asked) is True


def test_the_record_does_not_depend_on_which_attempt_s_directory_held_the_copy(tmp_path):
    inp, out = _plate(tmp_path)
    assert (_recorded_by_the_host(inp, out, "/stage/runs/run-1")
            == _recorded_by_the_host(inp, out, "/stage/runs/run-2"))


def test_a_directory_a_person_named_is_still_part_of_the_settings(tmp_path):
    """The other half of the rule: without the carried mark the directory names another blank, and a well
    corrected against it is not this run's."""
    from pipeline.cli_args import settings_key_from_argv
    from util.settings_digest import primary_settings_key, settings_match

    inp, out = _plate(tmp_path)
    flags = _local_argv(inp, out)[2:]
    named = primary_settings_key(settings_key_from_argv([*flags, "--background_dir=/blanks/day1"]))
    assert settings_match(named, settings_key_from_argv([*flags, "--background_dir=/blanks/day1"])) is True
    assert settings_match(named, settings_key_from_argv([*flags, "--background_dir=/blanks/day2"])) is False
    assert settings_match(named, settings_key_from_argv(flags)) is False
    # And the mark alone, with no directory, changes nothing.
    assert (primary_settings_key(settings_key_from_argv([*flags, "--background_dir_carried"]))
            == primary_settings_key(settings_key_from_argv(flags)))


def test_a_well_an_earlier_build_recorded_under_the_copy_s_path_is_found_by_the_run_that_knows_the_path(tmp_path):
    """Before the mark existed the host keyed the path itself. A batched run asks with its own run directory and
    each earlier attempt's as an alias (``remote_batch_resume._blank_argv``), so it still finds such a well; a
    question that names no path cannot, and recomputes it once."""
    from pipeline.cli_args import settings_key_from_argv
    from util.settings_digest import primary_settings_key, settings_match

    inp, out = _plate(tmp_path)
    earlier_build = [f for f in _host_flags(inp, out, "/stage/runs/run-1") if f != "--background_dir_carried"]
    recorded = primary_settings_key(settings_key_from_argv(earlier_build))
    flags = _local_argv(inp, out)[2:]
    same_run = [*flags, "--background_dir=/stage/runs/run-1/bg", "--background_dir_carried"]
    resumed = [*flags, "--background_dir=/stage/runs/run-2/bg", "--background_dir_carried",
               "--background_dir_alias=/stage/runs/run-1/bg"]
    other_run = [*flags, "--background_dir=/stage/runs/run-2/bg", "--background_dir_carried"]
    assert settings_match(recorded, settings_key_from_argv(same_run)) is True
    assert settings_match(recorded, settings_key_from_argv(resumed)) is True
    assert settings_match(recorded, settings_key_from_argv(other_run)) is False
    assert settings_match(recorded, settings_key_from_argv(flags)) is False
