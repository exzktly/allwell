"""A scheduled run asks Slurm for its node to itself when told to.

Without ``--exclusive`` Slurm places a second job on a node whenever it looks
to have room by what the jobs asked for, and on a cluster that does not count
memory (AWS ParallelCluster's default) two runs that each size their workers to
the whole node fit by CPU count. Two app instances running at once is the
ordinary way to get there. ``RemoteHost.exclusive_node`` makes every launch site send
``srun --exclusive``; the Remotes dialog has a checkbox for it. It is off by
default: reserving the whole node means every run on a power-saving cluster
boots a fresh node, so a node type that cannot boot stops every run.

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


def test_it_is_off_by_default_and_on_leaves_the_stored_command_alone():
    host = _host()
    assert host.exclusive_node is False
    assert host.launch_command == "srun --partition=gpu --time=1:00:00"
    on = replace(host, exclusive_node=True)
    assert on.compute_launcher == "srun --partition=gpu --time=1:00:00"
    assert on.launch_command == "srun --exclusive --partition=gpu --time=1:00:00"


def test_a_config_that_does_not_name_the_setting_does_not_reserve_the_node(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text('[remote.hpc]\nssh_alias = "hpc.example"\n'
                   'compute_launcher = "srun -p gpu"\n', encoding="utf-8")
    host = get_remote("hpc", path=cfg)
    assert host.exclusive_node is False
    assert host.launch_command == "srun -p gpu"


def test_a_config_saved_with_it_switched_off_stays_off(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text('[remote.hpc]\nssh_alias = "hpc.example"\n'
                   'compute_launcher = "srun -p gpu"\nexclusive_node = false\n', encoding="utf-8")
    assert get_remote("hpc", path=cfg).exclusive_node is False


def test_switching_it_on_survives_a_save_and_a_reload(tmp_path):
    cfg = tmp_path / "remotes.toml"
    assert "exclusive_node" not in _format_block(_host()), "the default is not written"
    write_remote(_host(exclusive_node=True), path=cfg)
    assert "exclusive_node = true" in cfg.read_text(encoding="utf-8")
    reloaded = get_remote("hpc", path=cfg)
    assert reloaded.exclusive_node is True
    assert reloaded.launch_command == "srun --exclusive --partition=gpu --time=1:00:00"


def test_the_scheduler_test_asks_for_the_node_a_run_would(monkeypatch):
    """The dialog's Test button must probe with the command a run sends, or it
    reports a node shape the run will not get."""
    from services.scheduler_probe import ProbeRun

    script = unpack_login_shell_command(ProbeRun(_host(exclusive_node=True)).argv[-1])
    assert "srun --exclusive --partition=gpu" in script
    script = unpack_login_shell_command(ProbeRun(_host()).argv[-1])
    assert "--exclusive" not in script


def test_the_batched_driver_asks_for_the_node_to_itself(tmp_path, monkeypatch):
    """A batched run holds one allocation for every batch; it is launched from
    its own site (``_BatchedRemoteProcess``), not the single-launch one."""
    from tests.test_batch_driver import _batched, _FakeProc, _stub_runner

    proc = _FakeProc()
    p = _batched(tmp_path, ["A01"])
    _stub_runner(p, proc, [])
    real = replace(_host(exclusive_node=True), remote_staging_dir="/r")
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


def test_the_dialog_checkbox_is_unticked_by_default_and_ticking_it_is_saved(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    import analyze.remote_dialog as ard

    dlg = ard.RemoteFormDialog(None, host=_host())
    qtbot.addWidget(dlg)
    assert not dlg._exclusive_cb.isChecked()
    assert dlg.result_host().exclusive_node is False

    dlg._exclusive_cb.click()
    host = dlg.result_host()
    assert host.exclusive_node is True
    write_remote(host, path=tmp_path / "remotes.toml")
    assert get_remote("hpc", path=tmp_path / "remotes.toml").exclusive_node is True

    reopened = ard.RemoteFormDialog(None, host=host)
    qtbot.addWidget(reopened)
    assert reopened._exclusive_cb.isChecked(), "a ticked remote reopened unticked"
