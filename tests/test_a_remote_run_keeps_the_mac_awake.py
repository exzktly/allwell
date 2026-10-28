"""A remote run keeps the Mac awake from its start to its end (review C9).

The app drives a remote run for its whole length — the heartbeat, each batch's
hand-off, every well's download — so a Mac that sleeps stops the run on the
cluster: the node is handed back between batches, or the batch suspended. The
run now holds ``caffeinate -i -s -w <app pid>`` (``services.sleep_guard``) for
as long as ``PipelineRunner._run_pipeline_remote`` runs, and releases it when
the run ends. These drive that real entry point with a stand-in
``caffeinate`` on ``PATH`` that records its arguments and stays up until it is
signalled, and read from the stand-in's own record whether the assertion was
held while the run was going and released once it was over.
"""

from __future__ import annotations

import os
import stat
import types
from pathlib import Path

import pytest

import services.sleep_guard as sleep_guard


def _stand_in(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "caffeinate"
    record = tmp_path / "caffeinate.log"
    exe.write_text("#!/bin/sh\n"
                   f'echo "start $$ $*" >> {record}\n'
                   f"trap 'echo \"stop $$\" >> {record}; exit 0' TERM\n"
                   "while :; do sleep 0.1; done\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return bindir


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _run_remote(monkeypatch, tmp_path, *, macos: bool, spawn_raises=None):
    """Drive the real ``_run_pipeline_remote`` with a stub remote; return what
    the stand-in recorded while the run was spawning, and afterwards."""
    import services.pipeline_runner as pr
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import LocalInputMode, ZippedInput

    monkeypatch.setenv("PATH", f"{_stand_in(tmp_path)}:{os.environ['PATH']}")
    monkeypatch.setattr(sleep_guard, "_is_macos", lambda: macos)
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    zipped = ZippedInput(zip_dir=in_dir, mode=LocalInputMode.ZIP, well_zips=(in_dir / "A01.zip",))
    monkeypatch.setattr(pr, "stage_local_zips", lambda *a, **k: zipped)
    record = tmp_path / "caffeinate.log"
    during: list[list[str]] = []

    class _Proc:
        pid = 1
        returncode = 0
        stdout = iter(())
        _ssh = None

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def spawn(*_a, **_k):
        import time
        deadline = time.monotonic() + (3 if macos else 0.3)     # the stand-in's shell writes as it starts
        while not record.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        lines = record.read_text().splitlines() if record.exists() else []
        during.append(lines)
        if spawn_raises is not None:
            raise spawn_raises
        return _Proc()

    r = pr.PipelineRunner()
    r._remote = types.SimpleNamespace(name="h")
    r._remote_runner = types.SimpleNamespace(
        estimate=lambda z: types.SimpleNamespace(format=lambda: "est"),
        spawn=spawn, verify_and_cleanup=lambda ok, **k: True,
        close_remote_job=lambda **k: None, cancel=lambda proc: None,
        can_request_suspend=False)
    opts = {"filename_schema": "experiment:channel:well:fov:timepoint", "filename_sep": "_",
            "fluor_tokens": ["GFP"], "nuclear_token": "DAPI", "csv_prefix": "measurements"}
    args = build_pipeline_args(Path("process_microscopy.py"), in_dir, out_dir, opts)
    r._run_pipeline_remote(in_dir, out_dir, args, opts)
    assert during, "the stub remote was never launched, so nothing was measured"
    after = record.read_text().splitlines() if record.exists() else []
    return during[0], after


@pytest.mark.parametrize("spawn_raises", [None, RuntimeError("upload failed")],
                         ids=["run-completes", "run-fails"])
def test_the_mac_is_kept_awake_for_the_run_and_let_sleep_after(monkeypatch, tmp_path, spawn_raises):
    during, after = _run_remote(monkeypatch, tmp_path, macos=True, spawn_raises=spawn_raises)
    assert len(during) == 1, f"the assertion was not held while the run was going: {during}"
    word, pid, *argv = during[0].split()
    assert word == "start"
    assert argv == ["-i", "-s", "-w", str(os.getpid())]
    assert after == [during[0], f"stop {pid}"], f"the assertion outlived the run: {after}"
    assert not _alive(int(pid))


def test_nothing_is_held_on_another_platform(monkeypatch, tmp_path):
    during, after = _run_remote(monkeypatch, tmp_path, macos=False)
    assert during == [[]] or during == []
    assert after == []


def test_a_mac_without_caffeinate_says_so(monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(sleep_guard, "_is_macos", lambda: True)
    monkeypatch.setattr(sleep_guard.shutil, "which", lambda _name: None)
    with sleep_guard.SleepGuard(log_fn=said.append):
        pass
    assert "caffeinate is not available, so this Mac may sleep during the run" in "".join(said)
