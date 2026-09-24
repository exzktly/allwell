"""A pipeline whose app has gone away winds down, and the restarted app waits for it.

The app launches a local pipeline in a session of its own, so that Stop can
signal the whole tree — which also means a crashed app left it running. Its
output went to a pipe nobody read, and it went on processing the plate. When the
user restarted the app and resumed the same plate, the new run's input grouping
packed and removed the ``in/<well>/`` folders the orphan was still reading —
its error files then read "No such file or directory: <plate>/in/<well>/…tif" —
and whichever run wrote a well's CSV last won.

These run real processes: an "app" that launches a pipeline stand-in (and the
stand-in a well worker in its own process group, as the pool does), then exits.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(os.name != "posix",
                                reason="POSIX reparents an orphan; Windows does not")


def test_a_run_whose_app_dies_suspends_itself_and_its_workers(tmp_path):
    out_dir = tmp_path / "out"
    worker = tmp_path / "worker.py"
    worker.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(_REPO)!r})
        from pipeline import suspend
        suspend.install_handlers()
        for _ in range(200):
            if suspend.is_suspended():
                open({str(tmp_path / "worker.suspended")!r}, "w").close()
                break
            time.sleep(0.05)
    """))
    pipeline = tmp_path / "pipeline.py"
    pipeline.write_text(textwrap.dedent(f"""
        import subprocess, sys, time
        sys.path.insert(0, {str(_REPO)!r})
        from pipeline import suspend
        suspend.install_handlers()
        w = subprocess.Popen([sys.executable, {str(worker)!r}])
        suspend.watch_parent({str(out_dir)!r}, poll_s=0.05)
        for _ in range(200):
            if suspend.is_suspended():
                open({str(tmp_path / "pipeline.suspended")!r}, "w").close()
                break
            time.sleep(0.05)
        w.wait(10)
    """))
    app = tmp_path / "app.py"
    app.write_text(textwrap.dedent(f"""
        import os, subprocess, sys, time
        subprocess.Popen([sys.executable, {str(pipeline)!r}],
                         start_new_session=True,
                         env={{**os.environ, "ALLWELL_PARENT_PID": str(os.getpid())}})
        time.sleep(0.8)          # the run is under way; then the app crashes
    """))
    subprocess.run([sys.executable, str(app)], timeout=30, check=True)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not (
            (tmp_path / "pipeline.suspended").exists()
            and (tmp_path / "worker.suspended").exists()):
        time.sleep(0.1)
    assert (tmp_path / "pipeline.suspended").exists(), (
        "the orphaned run went on processing after its app died")
    assert (tmp_path / "worker.suspended").exists(), (
        "the run suspended itself but its well worker never heard")


def test_a_run_whose_app_lives_keeps_going(tmp_path):
    """The control: a parent that is still there is not a reason to stop."""
    code = textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {str(_REPO)!r})
        from pipeline import suspend
        os.environ["ALLWELL_PARENT_PID"] = str(os.getppid())
        suspend.watch_parent({str(tmp_path / "out")!r}, poll_s=0.05)
        time.sleep(0.6)
        print("suspended", suspend.is_suspended())
        print("alive", suspend.live_pipeline_pid({str(tmp_path / "out")!r}) == os.getpid())
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=30)
    assert out.stdout.split() == ["suspended", "False", "alive", "True"], out.stderr


def test_a_stale_or_dead_heartbeat_is_not_a_live_run(tmp_path):
    from pipeline.suspend import ALIVE_FILENAME, ALIVE_STALE_S, live_pipeline_pid

    beat = tmp_path / ALIVE_FILENAME
    beat.write_text(str(os.getpid()))
    assert live_pipeline_pid(tmp_path) == os.getpid()
    old = time.time() - ALIVE_STALE_S - 5
    os.utime(beat, (old, old))
    assert live_pipeline_pid(tmp_path) is None, "a stale heartbeat still counted"
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    beat.write_text(str(dead.pid))
    assert live_pipeline_pid(tmp_path) is None, "a dead pid still counted"


def test_the_restarted_app_will_not_start_over_a_live_run(tmp_path, qtbot,
                                                         monkeypatch):
    """Through the real ``start_run``, which Run, Resume and the queue all use."""
    import zipfile

    import analyze.form
    from pipeline.suspend import ALIVE_FILENAME
    from services.run_spec import RunSpec

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    with zipfile.ZipFile(plate / "in" / "A01.zip", "w") as zf:
        zf.writestr("exp_DAPI_A01_F001_0h.tif", b"\0" * 64)
    (plate / "out").mkdir()
    (plate / "out" / ALIVE_FILENAME).write_text(str(os.getpid()))

    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    started: list = []
    monkeypatch.setattr(type(tab._runner), "start",
                        lambda self, *a, **k: started.append(a))
    spec = RunSpec.from_json({"opts": {
        "raw": str(plate), "filename_schema": "experiment:channel:well:fov:timepoint",
        "filename_sep": "_", "nuclear_token": "DAPI", "fluor_tokens": ["GFP"]}})
    error = tab.start_run(spec, pipeline=Path("pm.py"))
    assert started == [], "a run was started over a pipeline still writing the plate"
    assert error and "earlier session" in error and str(os.getpid()) in error


def test_the_app_s_real_spawn_tells_the_pipeline_who_to_watch():
    """The watch is armed only by this variable, so the app's spawn must set it."""
    from services.pipeline_service import spawn_pipeline

    proc = spawn_pipeline([sys.executable, "-c",
                           "import os; print(os.environ.get('ALLWELL_PARENT_PID'), os.getppid())"])
    out, _ = proc.communicate(timeout=30)
    named, parent = out.split()
    assert named == str(os.getpid()) == parent


def test_the_pipeline_arms_the_watch_on_its_output_dir(monkeypatch, tmp_path):
    import process_microscopy as pm

    armed: list = []
    monkeypatch.setattr(pm._suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(pm._suspend, "watch_parent",
                        lambda out, **k: armed.append(out))
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py", "--input_dir", str(tmp_path / "missing"),
        "--output_dir", str(tmp_path / "out")])
    with pytest.raises(SystemExit):
        pm.main()
    assert armed == [(tmp_path / "out").resolve()]


def test_an_orphan_already_suspending_is_not_signalled_again(tmp_path):
    """After its first SIGTERM the handler is back to the default, so a second
    one — the watch's own — would kill the run Suspend was letting finish."""
    pipeline = tmp_path / "pipeline.py"
    pipeline.write_text(textwrap.dedent(f"""
        import os, signal, sys, time
        sys.path.insert(0, {str(_REPO)!r})
        from pipeline import suspend
        suspend.install_handlers()
        os.kill(os.getpid(), signal.SIGTERM)       # the user's Suspend
        time.sleep(0.1)
        suspend.watch_parent({str(tmp_path / "out")!r}, poll_s=0.05)
        time.sleep(2.0)                             # the wind-down, app gone
        open({str(tmp_path / "survived")!r}, "w").close()
    """))
    app = tmp_path / "app.py"
    app.write_text(textwrap.dedent(f"""
        import os, subprocess, sys, time
        subprocess.Popen([sys.executable, {str(pipeline)!r}], start_new_session=True,
                         env={{**os.environ, "ALLWELL_PARENT_PID": str(os.getpid())}})
        time.sleep(0.5)
    """))
    subprocess.run([sys.executable, str(app)], timeout=30, check=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (tmp_path / "survived").exists():
        time.sleep(0.1)
    assert (tmp_path / "survived").exists(), "the watch killed a run that was winding down"
