"""An ``srun`` launcher that sends the job's output elsewhere is refused.

``-o`` / ``--output`` / ``-e`` / ``--error`` / ``--pty`` put everything the job
prints in a file on the cluster or on a pseudo-terminal of its own, and the app
reads the whole run — progress, per-well results, the batch driver's
hand-offs — off ``srun``'s stdout, so it would hear nothing and the run would
look hung. Refused at launch (``wrap_srun_launch``) and in the Remotes dialog.
"""

from __future__ import annotations

import pytest

from services.remote_srun_retry import (
    redirect_launcher_words,
    redirect_words_message,
)


# ── a launcher that sends the output elsewhere is refused (review E5) ───────

@pytest.mark.parametrize("launcher, words", [
    ("srun -p cpu -o job.log", ["-o"]),
    ("srun -p cpu -ojob.log", ["-ojob.log"]),
    ("srun --output=job.log -p cpu", ["--output=job.log"]),
    ("srun -p cpu --error /tmp/e", ["--error"]),
    ("srun -e err.txt --time=1:00:00", ["-e"]),
    ("srun --pty -p cpu", ["--pty"]),
    ("srun -p cpu --exclusive --exclude=n01 -x n02 --time=1:00:00", []),
    ("sbatch --wait -o x", []),
])
def test_output_redirection_in_an_srun_launcher_is_found(launcher, words):
    assert redirect_launcher_words(launcher) == words


@pytest.mark.parametrize("launcher", ["srun -p cpu --output=job.log", "srun --pty -p cpu"])
def test_a_launch_that_would_send_its_output_elsewhere_is_refused(launcher):
    from services.remote_commands import _compose_driver_cmd, _compose_remote_cmd

    with pytest.raises(ValueError, match="sends the run's output to a file or a terminal"):
        _compose_remote_cmd(launcher, "/r/run.pid", ["python", "pm.py"], cache_hit=False)
    with pytest.raises(ValueError, match="sends the run's output to a file or a terminal"):
        _compose_driver_cmd(launcher, "/r/run.pid", "echo driver")


@pytest.mark.qt
def test_the_remotes_dialog_will_not_save_such_a_launcher(qtbot, monkeypatch):
    import analyze.remote_dialog as ard

    written: list = []
    monkeypatch.setattr(ard, "write_remote", lambda host: written.append(host))
    said: list = []
    monkeypatch.setattr(ard.QMessageBox, "critical", lambda *a, **k: said.append(a[2]))
    dlg = ard.RemoteFormDialog(None, host=None)
    qtbot.addWidget(dlg)
    dlg._name_edit.setText("hpc")
    dlg._ssh_alias_edit.setText("hpc.example")
    dlg._compute_loc_combo.setCurrentIndex(1)
    dlg._compute_launcher_edit.setText("srun -p cpu --output=job.log")

    dlg._on_ok()

    assert written == [], "a launcher that hides the run's output was saved"
    assert said == [redirect_words_message("srun -p cpu --output=job.log")]
