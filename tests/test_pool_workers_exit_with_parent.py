"""A spawned pool worker ends when the process that spawned it is killed.

Without this, a ``ProcessPoolExecutor`` worker whose parent dies waits on the
call queue for ever — it holds the write end of the pipe it reads — and on
Windows every Stop kills the pipeline's own process and nothing below it. Each
orphaned well worker kept TensorFlow, its model and a well's images committed
until the machine restarted. See ``pipeline.suspend.exit_with_parent``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests._repo_scan import parse_source

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(os.name != "posix", reason="kills by pid and reads /proc-free liveness via kill(0)")

#: Starts the real scan pool on two slow items, records its workers' pids, then
#: SIGKILLs itself — what TerminateProcess does to the pipeline on a Windows Stop.
_SCRIPT = textwrap.dedent("""
    import multiprocessing, os, signal, sys, threading, time
    sys.path.insert(0, {root!r})
    import _slow_item
    from pipeline import scan_pool
    {patch}

    def _kill_once_started():
        while len(multiprocessing.active_children()) < 2:
            time.sleep(0.05)
        time.sleep(1.0)
        pids = [p.pid for p in multiprocessing.active_children() if "resource_tracker" not in (p.name or "")]
        with open({out!r}, "w") as f:
            f.write(" ".join(map(str, pids)))
        os.kill(os.getpid(), signal.SIGKILL)

    if __name__ == "__main__":
        threading.Thread(target=_kill_once_started, daemon=True).start()
        scan_pool._across_processes(_slow_item.slow, [1, 2, 3, 4], 2, None)
""")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it holds no memory.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _orphans_after_parent_killed(tmp: Path, patch: str = "", grace: float = 10.0) -> list[int]:
    """The pids of the pool's workers still alive *grace* seconds after their parent was killed."""
    (tmp / "_slow_item.py").write_text("import time\ndef slow(x):\n    time.sleep(60)\n    return x\ndef noop():\n    pass\n")
    out = tmp / "pids.txt"
    script = tmp / "run.py"
    script.write_text(_SCRIPT.format(root=str(ROOT), out=str(out), patch=patch))
    env = {**os.environ, "PYTHONPATH": f"{tmp}{os.pathsep}{ROOT}"}
    # Output to a file, not a pipe: an orphan holding a pipe open would hang this test.
    with open(tmp / "log.txt", "w") as log:
        subprocess.run([sys.executable, str(script)], cwd=tmp, env=env, stdout=log, stderr=log, timeout=60)
    pids = [int(p) for p in out.read_text().split()]
    assert len(pids) == 2, (tmp / "log.txt").read_text()
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.1)
    survivors = [p for p in pids if _alive(p)]
    for p in survivors:
        os.kill(p, 9)
    return survivors


def test_scan_workers_end_when_their_parent_is_killed(tmp_path):
    assert _orphans_after_parent_killed(tmp_path) == []


def test_without_the_watch_they_would_not(tmp_path):
    """The control: the same pool without the initializer leaves both workers behind.

    Watched for 2 s rather than the 10 s the watched pool is given: an orphan here sleeps 60 s in its item, so every
    second of the window is waited out in full, while the watched case takes
    about 1 s from start-up to the last worker gone."""
    patch = "scan_pool.pool_worker_initializer = _slow_item.noop"
    assert len(_orphans_after_parent_killed(tmp_path, patch, grace=2.0)) == 2


def test_the_pipeline_s_well_workers_watch_their_parent_first():
    """``_well_pool`` runs ``_worker_init``, whose first act is the watch — before the TensorFlow import."""
    tree = parse_source(ROOT / "process_microscopy.py")
    pool = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_well_pool")
    assert "initializer=_worker_init" in ast.unparse(pool)
    init = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_worker_init")
    calls = [n.func.id for n in ast.walk(init) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert calls.index("_exit_with_parent") < calls.index("_install_suspend")
    tf = [n.lineno for n in ast.walk(init) if isinstance(n, (ast.Import, ast.ImportFrom))
          and "tensorflow" in ast.unparse(n)]
    watch = next(n.lineno for n in ast.walk(init) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "_exit_with_parent")
    assert all(watch < line for line in tf)
