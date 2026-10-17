"""Stop ends a batched remote run that is waiting for its node.

Reported: Stop said it had stopped the pipeline, and the log went on saying it
was still waiting for the Slurm node. Once a batched run has requested its node,
its ``pid`` is the driver ssh's, which runs in a process group of its own — so
the Stop's signal killed that ssh and never reached the wrapper. The wrapper's
stop flag stayed clear, its loop went on uploading the batch, and the run thread
never reported back, while the dashboard's scheduler clock kept printing
"still waiting for the scheduler".
"""

from __future__ import annotations

import subprocess
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_batch_driver import _batched  # noqa: E402


def test_stop_sets_the_batched_run_s_own_stop_flag(tmp_path, monkeypatch):
    import services.pipeline_runner as pr

    p = _batched(tmp_path, ["A01"])
    # The driver ssh, holding the node request: its own process group, as
    # `own_process_group_kwargs` launches it.
    driver = subprocess.Popen(["sleep", "30"], start_new_session=True)
    p._current_proc = driver
    # The batch loop, which ends only on its own stop flag — here, as in the
    # real loop between upload chunks.
    p._worker = threading.Thread(target=lambda: p._stop.wait(10), daemon=True)
    p._worker.start()
    try:
        runner = pr.PipelineRunner()
        runner._proc = p
        runner._remote_runner = types.SimpleNamespace(cancel=lambda inner: None)
        runner.stop()
        p._worker.join(5)
        assert p._stop.is_set(), (
            "the Stop killed the driver ssh but never reached the batched run")
        assert not p._worker.is_alive()
    finally:
        if driver.poll() is None:
            driver.kill()
        driver.wait(5)
        p._stop.set()


def test_stop_ends_the_scheduler_wait_on_the_dashboard(qtbot, monkeypatch):
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab._runner, "stop", lambda **k: None)
    tab._on_scheduler("queued", "4242", "srun -p cpu")
    assert tab._sched_timer.isActive()
    tab._stop()
    assert not tab._sched_timer.isActive(), (
        "the 'still waiting for the scheduler' clock outlived the Stop")
    assert "Waiting for the scheduler" not in tab._dashboard._status.text()
