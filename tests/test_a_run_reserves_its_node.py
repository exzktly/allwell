"""A scheduled run asks Slurm for its node to itself, unless told not to.

Without ``--exclusive`` Slurm places a second job on a node whenever it looks
to have room by what the jobs asked for, and on a cluster that does not count
memory (AWS ParallelCluster's default) two runs that each size their workers to
the whole node fit by CPU count. Two app instances running at once is the
ordinary way to get there. ``RemoteHost.exclusive_node`` (on by default, which
configs saved before it existed get too) makes every launch site send
``srun --exclusive``; the Remotes dialog has a checkbox for it.

The single-launch path is driven in ``test_remote_runner.py``
(``test_a_scheduled_spawn_asks_for_the_node_to_itself``); this file covers the
word itself, the batched driver, the scheduler test, the dialog, and the file.
"""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from services.remote_commands import unpack_login_shell_command
from services.remote_config import RemoteHost, _format_block, get_remote, write_remote
from services.remote_srun_retry import with_exclusive


@pytest.mark.parametrize("launcher, expected", [
    ("srun --partition=gpu --time=4:00:00", "srun --exclusive --partition=gpu --time=4:00:00"),
    ("  /usr/bin/srun\t-p q", "  /usr/bin/srun --exclusive -p q"),
    ("srun", "srun --exclusive"),
    ('srun --comment="two words" -p q', 'srun --exclusive --comment="two words" -p q'),
    # Already decided by the user: left exactly as written.
    ("srun --exclusive -p q", "srun --exclusive -p q"),
    ("srun -p q --exclusive=user", "srun -p q --exclusive=user"),
    ("srun --oversubscribe -p q", "srun --oversubscribe -p q"),
    ("srun -s -p q", "srun -s -p q"),
    # Not srun, or not parseable: nothing to add the word to.
    ("sbatch -p q", "sbatch -p q"),
    ("", ""),
    ('srun --comment="unclosed', 'srun --comment="unclosed'),
])
def test_the_word_is_added_once_and_only_to_srun(launcher, expected):
    assert with_exclusive(launcher) == expected


def _host(**kw) -> RemoteHost:
    return RemoteHost(name="hpc", ssh_alias="hpc.example",
                      compute_launcher="srun --partition=gpu --time=1:00:00", **kw)


def test_it_is_on_by_default_and_leaves_the_stored_command_alone():
    host = _host()
    assert host.exclusive_node is True
    assert host.compute_launcher == "srun --partition=gpu --time=1:00:00"
    assert host.launch_command == "srun --exclusive --partition=gpu --time=1:00:00"
    assert replace(host, exclusive_node=False).launch_command == host.compute_launcher


def test_a_config_saved_before_the_setting_existed_reserves_the_node(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text('[remote.hpc]\nssh_alias = "hpc.example"\n'
                   'compute_launcher = "srun -p gpu"\n', encoding="utf-8")
    host = get_remote("hpc", path=cfg)
    assert host.exclusive_node is True
    assert host.launch_command == "srun --exclusive -p gpu"


def test_switching_it_off_survives_a_save_and_a_reload(tmp_path):
    cfg = tmp_path / "remotes.toml"
    assert "exclusive_node" not in _format_block(_host()), "the default is not written"
    write_remote(_host(exclusive_node=False), path=cfg)
    assert "exclusive_node = false" in cfg.read_text(encoding="utf-8")
    reloaded = get_remote("hpc", path=cfg)
    assert reloaded.exclusive_node is False
    assert reloaded.launch_command == "srun --partition=gpu --time=1:00:00"


def test_the_scheduler_test_asks_for_the_node_a_run_would(monkeypatch):
    """The dialog's Test button must probe with the command a run sends, or it
    reports a node shape the run will not get."""
    from services.scheduler_probe import ProbeRun

    script = unpack_login_shell_command(ProbeRun(_host()).argv[-1])
    assert "srun --exclusive --partition=gpu" in script
    script = unpack_login_shell_command(ProbeRun(_host(exclusive_node=False)).argv[-1])
    assert "--exclusive" not in script


def test_the_batched_driver_asks_for_the_node_to_itself(tmp_path, monkeypatch):
    """A batched run holds one allocation for every batch; it is launched from
    its own site (``_BatchedRemoteProcess``), not the single-launch one."""
    from tests.test_batch_driver import _batched, _FakeProc, _stub_runner

    proc = _FakeProc()
    p = _batched(tmp_path, ["A01"])
    _stub_runner(p, proc, [])
    real = replace(_host(), remote_staging_dir="/r")
    p._runner.host = real
    spawns: list[list[str]] = []
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: spawns.append(argv) or proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    proc.batch_finished(p, 1)
    proc.finish()
    worker.join(timeout=20)

    assert not worker.is_alive() and len(spawns) == 1
    script = unpack_login_shell_command(spawns[0][-1])
    assert "srun --exclusive --partition=gpu" in script


def test_the_dialog_checkbox_is_ticked_by_default_and_unticking_it_is_saved(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    import analyze.remote_dialog as ard

    dlg = ard.RemoteFormDialog(None, host=_host())
    qtbot.addWidget(dlg)
    assert dlg._exclusive_cb.isChecked()
    assert dlg.result_host().exclusive_node is True

    dlg._exclusive_cb.click()
    host = dlg.result_host()
    assert host.exclusive_node is False
    write_remote(host, path=tmp_path / "remotes.toml")
    assert get_remote("hpc", path=tmp_path / "remotes.toml").exclusive_node is False

    reopened = ard.RemoteFormDialog(None, host=host)
    qtbot.addWidget(reopened)
    assert not reopened._exclusive_cb.isChecked(), "an unticked remote reopened ticked"
