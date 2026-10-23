"""Test detection runs in a child process, so a segmenter that kills its process cannot kill the app (tracker F23).

The end-to-end half — a real child on a rendered well, and a real crash — is in ``test_nanowell_preview.py`` (the
nanowell lane). These drive :func:`run_preview` with a stand-in for ``subprocess.run`` that plays the child.
"""

from __future__ import annotations

import pickle
import sys
import types
from pathlib import Path

import pytest

import services.nanowell_preview_job as job_mod

CRASH = 0xC0000138


def _child(behaviour):
    """A ``subprocess.run`` stand-in. *behaviour(job, refuse)* returns the outcome to write, or an int exit code
    to die with and write nothing."""
    calls = []

    def runner(argv, **_kwargs):
        job_path, out_path = Path(argv[-2]), Path(argv[-1])
        with open(job_path, "rb") as fh:
            job, refuse = pickle.load(fh)
        calls.append(refuse)
        outcome = behaviour(job, refuse)
        if isinstance(outcome, int):
            return types.SimpleNamespace(returncode=outcome, stderr=b"Windows fatal exception: code 0xc0000138")
        with open(out_path, "wb") as fh:
            pickle.dump(outcome, fh)
        return types.SimpleNamespace(returncode=0, stderr=b"")
    return runner, calls


def test_a_child_that_finishes_hands_its_result_back_and_runs_once():
    runner, calls = _child(lambda job, refuse: ("ok", {"well": job["well"]}))
    assert job_mod.run_preview({"well": "D07"}, runner=runner) == {"well": "D07"}
    assert calls == [None]                             # one run, with the segmenter allowed to load


def test_a_child_that_crashes_is_run_again_with_the_segmenter_refused_and_the_crash_named():
    runner, calls = _child(lambda job, refuse: CRASH if refuse is None else ("ok", refuse))
    reason = job_mod.run_preview({"well": "D07"}, runner=runner)
    assert calls[0] is None and calls[1] == reason
    assert "exit code 0xc0000138" in reason and "STATUS_ORDINAL_NOT_FOUND" in reason
    assert "the app was not affected" in reason


def test_a_child_that_crashes_without_the_segmenter_too_is_a_failure_that_says_so():
    runner, calls = _child(lambda job, refuse: CRASH)
    with pytest.raises(job_mod.PreviewCrashed) as raised:
        job_mod.run_preview({"well": "D07"}, runner=runner)
    assert len(calls) == 2 and "not Cellpose" in str(raised.value) and "0xc0000138" in str(raised.value)


def test_a_preview_that_raised_in_the_child_carries_the_child_s_message():
    runner, _ = _child(lambda job, refuse: ("error", "ValueError: no tiles"))
    with pytest.raises(job_mod.PreviewFailed, match=r"^ValueError: no tiles$"):
        job_mod.run_preview({}, runner=runner)


def test_the_refusing_segmenter_is_the_cellpose_unavailable_case_the_preview_falls_back_on():
    from pipeline.cellpose import CellposeUnavailable
    from pipeline.nanowell_preview import _missing_import

    with pytest.raises(CellposeUnavailable) as raised:
        job_mod._refusing_segmenter("it crashed")("an image")
    assert _missing_import(raised.value) == "it crashed"


@pytest.mark.parametrize(("code", "text"), [
    (CRASH, "exit code 0xc0000138, STATUS_ORDINAL_NOT_FOUND"),
    (0xC0000005, "exit code 0xc0000005, STATUS_ACCESS_VIOLATION"),
    (0xC0001234, "exit code 0xc0001234"),
    (-11, "killed by signal 11"),
    (1, "exit code 1"),
    (None, "no exit status"),
])
def test_an_exit_status_is_described(code, text):
    assert job_mod.describe_exit(code).split(":")[0] == text


def test_the_child_command_from_source_runs_this_file(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cmd = job_mod.child_command(Path("j"), Path("o"))
    assert cmd == [sys.executable, str(Path(job_mod.__file__).resolve()), "j", "o"]


def test_the_child_command_frozen_uses_the_launcher_sentinel(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert job_mod.child_command(Path("j"), Path("o")) == [sys.executable, "--run-nanowell-preview", "j", "o"]


def test_the_launcher_dispatches_the_sentinel_to_the_child_entry():
    from tests._repo_scan import REPO_ROOT

    source = (REPO_ROOT / "all_well_launcher.py").read_text(encoding="utf-8")
    assert f'sys.argv[1] == "{job_mod.FROZEN_SENTINEL}"' in source
    assert "from services.nanowell_preview_job import main as preview_main" in source
    assert source.index(job_mod.FROZEN_SENTINEL) < source.index("from allwell import main")


def test_the_gui_never_measures_in_its_own_process():
    """The window's worker calls the child runner, and nothing in the GUI module reaches the analysis directly."""
    from tests._repo_scan import REPO_ROOT, parse_source
    import ast

    tree = parse_source(REPO_ROOT / "analyze" / "nanowell_preview.py")
    imported = {(node.module, alias.name) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                for alias in node.names}
    assert ("services.nanowell_preview_job", "run_preview") in imported
    assert not any(module and module.startswith("pipeline.") and name in {"preview_timepoint", "measure_timepoint"}
                   for module, name in imported)


def test_the_child_entry_writes_an_error_outcome_rather_than_dying(tmp_path, monkeypatch):
    def boom(job, segmenter=None):
        raise ValueError(f"no tiles in {job['well']}")

    monkeypatch.setattr(job_mod, "compute", boom)
    job_path, out_path = tmp_path / "job.pkl", tmp_path / "out.pkl"
    job_path.write_bytes(pickle.dumps(({"well": "D07"}, None)))
    job_mod.serve(job_path, out_path)
    assert pickle.loads(out_path.read_bytes()) == ("error", "ValueError: no tiles in D07")
