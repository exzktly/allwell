"""A remote run split into batches can be limited to named wells.

Reported: running a subset of wells (Only these wells) failed with "--only_wells names well(s) not in the input",
though every well uploaded. A batched remote run gives every batch the same pipeline argv, so ``--only_wells``
named the whole subset while each batch's input held only its share; every batch refused the others' wells. The
batch argv now carries ``--only_wells_absent_ok`` (``remote_commands.batch_pipeline_argv``), with which the pipeline
skips a named well that is in another batch; the app still refuses a named well the plate lacks before uploading.
"""

from __future__ import annotations

import logging

import pytest

from pipeline.cli_args import build_parser
from pipeline.well_skip import scope_to_only_wells
from services.remote_commands import batch_pipeline_argv
from tests.test_only_wells_run import _plate, _run_main, stubbed_segmenter  # noqa: F401

BATCH = [("A01", "/in/A01.zip"), ("A02", "/in/A02.zip")]          # this batch's share of the subset


class _Refused(Exception):
    pass


def _refuse(message):
    raise _Refused(message)


def test_one_batch_runs_the_named_wells_it_holds_and_says_the_rest_are_another_batch_s(caplog):
    with caplog.at_level(logging.INFO):
        zips, folders = scope_to_only_wells(BATCH, [], ["A01", "B05", "C07"], refuse=_refuse,
                                            log=logging.getLogger("t"), absent_ok=True)
    assert zips == [("A01", "/in/A01.zip")] and folders == []
    assert "B05, C07 not in this input (another batch's)" in caplog.text


def test_without_the_flag_a_named_well_missing_from_the_input_is_still_refused():
    with pytest.raises(_Refused, match="--only_wells names well\\(s\\) not in the input: B05"):
        scope_to_only_wells(BATCH, [], ["A01", "B05"], refuse=_refuse, log=logging.getLogger("t"))


def test_every_batch_of_a_subset_run_is_told_to_skip_the_other_batches_wells():
    argv = ["python", "pm.py", "--input_dir", "/r/in", "--only_wells", "A01", "B05", "--workers", "2"]
    batched = batch_pipeline_argv(argv)
    assert batched[:len(argv)] == argv
    assert batched[len(argv):] == ["--no_auto_threshold", "--only_wells_absent_ok"]
    assert batch_pipeline_argv(batched) == batched                       # never twice
    plain = batch_pipeline_argv(["python", "pm.py", "--input_dir", "/r/in"])
    assert "--only_wells_absent_ok" not in plain                         # a whole-plate run is unchanged


def test_the_pipeline_reads_the_flag():
    args = build_parser().parse_args(["--input_dir", "/i", "--output_dir", "/o", "--only_wells", "A01",
                                      "--only_wells_absent_ok"])
    assert args.only_wells == ["A01"] and args.only_wells_absent_ok is True


def test_the_pipeline_runs_the_named_wells_this_batch_holds_and_skips_the_rest(
        tmp_path, monkeypatch, stubbed_segmenter):  # noqa: F811
    """Through ``process_microscopy.main``: a batch holding B02 and C01, told the whole subset C01 + H12 (H12 being
    in another batch), runs C01 alone — and without the flag the same batch is refused, as a local run still is."""
    plate = _plate(tmp_path)
    ran: list[str] = []
    _run_main(monkeypatch, plate, "--only_wells", "C01", "H12", "--only_wells_absent_ok", ran=ran)
    assert ran == ["C01"]
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, _plate(tmp_path / "again"), "--only_wells", "C01", "H12", check=False)
    assert exc.value.code == 2


def test_the_batched_launch_tells_every_batch_to_skip_the_others_wells(tmp_path, monkeypatch):
    """The command the app actually launches for a batched run, not the helper alone: the one pipeline argv in the
    driver script carries ``--only_wells_absent_ok`` beside the run's ``--only_wells``."""
    import subprocess
    import threading

    from tests.test_batch_driver import _batched, _FakeProc, _stub_runner

    proc = _FakeProc()
    p = _batched(tmp_path, ["A01", "A02"])
    _stub_runner(p, proc, [])
    p._runner._build_remote_argv = lambda *a, **k: ["python", "pm.py", "--only_wells", "A01", "A02"]
    spawns = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: spawns.append(argv) or proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    for i in (1, 2):
        proc.say(f"[remote] batch-driver: batch {i} finished rc=0\n")
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive() and len(spawns) == 1
    from services.remote_commands import unpack_login_shell_command

    script = unpack_login_shell_command(" ".join(str(a) for a in spawns[0]))
    assert "--only_wells A01 A02 --no_auto_threshold --only_wells_absent_ok" in script, script[script.find("pm.py"):][:160]
