"""*Request a node* on a host whose Slurm tools are not on the control shell's ``PATH``.

The control scripts are piped to ``ssh <host> bash -s``: neither a login nor an interactive shell, so it never reads
``/etc/profile`` or ``~/.bash_profile``, where a cluster puts Slurm on ``PATH`` when it is not in ``/usr/bin``.
*Test scheduler* and a run reach ``srun`` through the host's scheduler command, so both worked while *Request a node*
reported ``bash: line 126: sbatch: command not found`` -- line 126 being the ``sbatch`` call of the submit script.

These run the real scripts under ``bash`` against the stand-in cluster of ``tests/_hold_cluster.py``, with the stand-in
programs **taken off** the PATH the control shell gets, which is the one thing the other hold tests never do (they
put them on it, so every one of them passes on a host where ``sbatch`` is found).

Wrong implementations these reject: the prelude never applied (the first test is that control, failing as reported);
applied to the submit script only, leaving the state read and the cancel to say ``scontrol: command not found``;
looking only beside the launcher's ``srun``, which a host that names a bare ``srun`` does not give.
"""

from __future__ import annotations

import os
import re
import shutil
import stat

import pytest

from services.remote_config import RemoteHost
from services.remote_hold import (
    GrantedNode,
    cancel_script,
    compose_hold_script,
    hold_paths,
    slurm_path_prelude,
    state_script,
    submit_script,
)

from tests._hold_cluster import HOST, Cluster, Script, granted_files

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def cluster(tmp_path):
    """The stand-in host, with its Slurm programs *not* on the control shell's PATH."""
    c = Cluster(tmp_path)
    bare = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if p and not os.path.exists(os.path.join(p, "sbatch")))
    c.env = {**c.env, "PATH": bare}
    assert shutil.which("sbatch", path=bare) is None
    return c


def _submit(host: RemoteHost) -> str:
    paths = hold_paths(host)
    return submit_script(paths, compose_hold_script(paths, limit_s=14400, idle_release_s=1800), ["--partition=gpu"],
                         limit_s=14400)


def test_without_the_prelude_the_submit_script_fails_as_reported(cluster):
    """The control: the script alone, on this PATH, is what the user saw."""
    host = RemoteHost(**HOST)
    result = cluster._ssh_script(_submit(host), check=False)
    assert result.returncode == 127
    assert "sbatch: command not found" in result.stderr
    # ...and it is the `sbatch` line, the one the report named.
    # The report said line 126; the hold script has grown since, so the line is read off the script, not pinned.
    (line,) = re.findall(r"line (\d+): sbatch: command not found", result.stderr)
    assert _submit(host).splitlines()[int(line) - 1].lstrip().startswith("_aw_out=$(sbatch")


def test_sbatch_is_found_beside_the_srun_the_scheduler_command_names(cluster):
    launcher = f"{cluster.bin}/srun --partition=gpu --gres=gpu:1"
    host = RemoteHost(**{**HOST, "compute_launcher": launcher})
    result = cluster._ssh_script(slurm_path_prelude(launcher) + _submit(host), check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "4001"
    (args,) = cluster.sbatch_calls()
    assert "--partition=gpu" in args


def _login_shell(tmp_path, *, bin_dir, banner: str) -> str:
    """A stand-in ``$SHELL``: given ``-lc <cmd>`` it runs *cmd* with Slurm on PATH, after printing a profile banner."""
    shell = tmp_path / "loginshell"
    shell.write_text(f'#!/bin/bash\n[ "$1" = "-lc" ] || exit 64\necho "{banner}"\n'
                     f'export PATH="{bin_dir}:$PATH"\nexec bash -c "$2"\n', encoding="utf-8")
    shell.chmod(shell.stat().st_mode | stat.S_IXUSR)
    return str(shell)


def test_sbatch_is_found_through_the_users_login_shell_when_the_launcher_names_a_bare_srun(cluster, tmp_path):
    cluster.env["SHELL"] = _login_shell(tmp_path, bin_dir=cluster.bin, banner="Welcome to the cluster. PATH=/not/this")
    host = RemoteHost(**HOST)                               # "srun --partition=gpu ...": nothing to look beside
    result = cluster._ssh_script(slurm_path_prelude(host.launch_command) + _submit(host), check=False)
    assert result.returncode == 0, result.stderr
    # The profile's banner did not reach the scripts' own output, which is parsed: the job id is the whole of it.
    assert result.stdout.strip() == "4001"


def test_a_host_with_slurm_already_on_the_path_is_left_alone(cluster):
    cluster.env["PATH"] = f"{cluster.bin}{os.pathsep}{cluster.env['PATH']}"
    host = RemoteHost(**HOST)
    before = cluster._ssh_script('printf %s "$PATH"', check=True).stdout
    after = cluster._ssh_script(slurm_path_prelude(host.launch_command) + 'printf %s "$PATH"', check=True).stdout
    assert after == before


def test_every_control_script_reads_and_cancels_through_the_prelude_not_only_the_submit(cluster, tmp_path):
    """The whole path a request takes -- the partition's limit, the submit, the state reads and the cancel -- on a
    PATH with no Slurm on it, through ``GrantedNode``. A prelude on the submit alone gets as far as the job id and then
    reads its state as ``scontrol: command not found``."""
    cluster.env["SHELL"] = _login_shell(tmp_path, bin_dir=cluster.bin, banner="motd")
    host = RemoteHost(**HOST)
    (cluster.fake / "sinfo.out").write_text("12:00:00\n", encoding="utf-8")
    sleep = Script(cluster, [lambda: granted_files(cluster, host), ("RUNNING", "None", "gpu-node-1")])
    lines: list[str] = []
    node = GrantedNode(host, cluster, log_fn=lines.append, sleep=sleep, clock=sleep.clock)
    try:
        state = node.request()
        assert state.phase in ("granted", "busy") and state.node == "gpu-node-1"
        assert state.slurm_state == "RUNNING"                # read by `scontrol`, which is not on the PATH either
        (args,) = cluster.sbatch_calls()
        assert "--time=12:00:00" in args                      # the partition's limit, read by `sinfo`
        node.release("test over")
        assert any(entry.endswith(" 4001") for entry in cluster.scancels()), cluster.scancels()
    finally:
        node.release("test over")


def test_the_scripts_handed_to_the_host_all_start_with_the_prelude(cluster):
    seen: list[str] = []
    real = cluster._ssh_script

    def spy(script, **kw):
        seen.append(script)
        return real(script, **kw)

    cluster._ssh_script = spy
    host = RemoteHost(**HOST)
    node = GrantedNode(host, cluster, log_fn=lambda _l: None)
    paths = hold_paths(host)
    for script in (state_script(paths), cancel_script(paths, "4001")):
        node._script(script, check=False)
    prelude = slurm_path_prelude(host.launch_command)
    assert len(seen) == 2 and all(s.startswith(prelude) for s in seen)
