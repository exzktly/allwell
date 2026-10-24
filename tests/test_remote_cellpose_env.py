"""A Cellpose run on a remote host uses the ``allwell-cellpose`` env when the host has it, ``allwell`` when it does not.

Asked for: Cellpose-SAM in bfloat16 on the remote. ``allwell`` holds TensorFlow 2.13, which caps Torch at 2.0.1, and
Torch 2.0 cannot run the model in bfloat16 (``pipeline.cellpose.BF16_MIN_TORCH``). The bootstrap now installs a second
env with a newer Torch (``requirements-remote-cellpose.txt``), and a run that segments with Cellpose — every nanowell
run, and the Cellpose methods — prefers it. The choice is made on the remote, by the env guard, so a host not yet
bootstrapped for it still runs (in ``allwell``, float32, with a note saying so).

Held here: which runs prefer it, the argv that reaches the remote, and the guard's choice run under bash against a
stub conda (``tests/test_remote_conda_env.py``'s, which owns the conda side of the answer).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from services.remote_commands import _compose_remote_cmd
from services.remote_conda import (
    CELLPOSE_CONDA_ENV, DEFAULT_ENV_SELECTOR, _conda_env_guard, cellpose_env_selector, runs_cellpose,
)
from services.remote_config import DEFAULT_CONDA_ENV, RemoteHost
from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner
from services.remote_transport import _remote_join
from tests.test_remote_conda_env import _make_env, _stub_conda, remote_home  # noqa: F401

_NEEDS_BASH = pytest.mark.skipif(shutil.which("bash") is None, reason="needs a POSIX shell to run the guard")
_PREFER = ("prefer", f"{CELLPOSE_CONDA_ENV} {DEFAULT_CONDA_ENV}")
_BASE = ["python", "pm.py", "--input_dir", "/in", "--output_dir", "/out"]


@pytest.mark.parametrize("argv, cellpose", [
    ([*_BASE, "--nanowell_format", "100um"], True),
    ([*_BASE, "--nanowell_format=50um"], True),
    ([*_BASE, "--segmentation_method", "cellpose_labelfree"], True),
    ([*_BASE, "--segmentation_method=cellpose"], True),
    ([*_BASE, "--segmentation_method", "stardist"], False),
    (_BASE, False),
])
def test_which_runs_segment_with_cellpose(argv, cellpose):
    assert runs_cellpose(argv) is cellpose
    assert cellpose_env_selector(DEFAULT_ENV_SELECTOR, argv) == (_PREFER if cellpose else DEFAULT_ENV_SELECTOR)


def test_a_host_whose_remote_python_names_an_env_keeps_it_for_cellpose_too():
    """An explicit choice: nothing here can know what that env holds."""
    own = ("prefix", "/opt/conda/envs/mine")
    assert cellpose_env_selector(own, [*_BASE, "--nanowell_format", "100um"]) == own


def _argv(host, local):
    runner = RemoteRunner(host)
    return runner, runner._build_remote_argv(local, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP)


def test_the_remote_argv_runs_a_nanowell_run_in_the_env_the_guard_resolved_and_others_by_name():
    host = RemoteHost(name="h", ssh_alias="h")
    runner, argv = _argv(host, [*_BASE, "--nanowell_format", "100um"])
    assert runner.env_selector == _PREFER
    assert argv[:5] == ["conda", "run", "--no-capture-output", "-p", '"$AW_PREFIX"']
    runner, argv = _argv(host, [*_BASE, "--segmentation_method", "stardist"])
    assert runner.env_selector == DEFAULT_ENV_SELECTOR and argv[:5] == [
        "conda", "run", "--no-capture-output", "-n", DEFAULT_CONDA_ENV]
    # The same runner, a Cellpose run after a StarDist one: chosen per run, not kept from the last.
    runner._build_remote_argv([*_BASE, "--nanowell_format", "100um"], "/r/in", "/r/out", OutputFilter.FULL,
                              LocalInputMode.ZIP)
    assert runner.env_selector == _PREFER


def _run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)


def _stub_conda_run(binder):
    """The stub conda answers ``run`` too, printing the env it was asked to run in."""
    script = (binder / "conda").read_text().replace('esac\n', '  "run --no-capture-output") echo "CONDA-RUN $4" ;;\nesac\n')
    (binder / "conda").write_text(script)


@_NEEDS_BASH
def test_the_guard_runs_a_cellpose_run_in_the_cellpose_env_where_the_host_has_it(remote_home, tmp_path):  # noqa: F811
    _, binder = remote_home
    allwell = _make_env(tmp_path / "envs" / "allwell", valid=True)
    cellpose = _make_env(tmp_path / "envs" / CELLPOSE_CONDA_ENV, valid=True)
    _stub_conda(binder, envs={"base": "/opt/conda", "allwell": str(allwell), CELLPOSE_CONDA_ENV: str(cellpose)})
    _stub_conda_run(binder)
    # The guard, then the argv as the runner quotes it for the remote shell: the prefix reaches conda resolved.
    argv = ["conda", "run", "--no-capture-output", "-p", '"$AW_PREFIX"', "python", "pm.py"]
    r = _run(_conda_env_guard(_PREFER) + "; " + _remote_join(argv))
    assert r.returncode == 0, r.stderr
    assert f"CONDA-RUN {cellpose}" in r.stdout
    assert f"conda env: {CELLPOSE_CONDA_ENV} (Cellpose, Torch with bfloat16)" in r.stdout


@_NEEDS_BASH
def test_a_host_without_it_runs_in_allwell_and_says_why(remote_home, tmp_path):  # noqa: F811
    _, binder = remote_home
    allwell = _make_env(tmp_path / "envs" / "allwell", valid=True)
    _stub_conda(binder, envs={"base": "/opt/conda", "allwell": str(allwell)})
    _stub_conda_run(binder)
    r = _run(_conda_env_guard(_PREFER) + '; conda run --no-capture-output -p "$AW_PREFIX" python pm.py')
    assert r.returncode == 0, r.stderr
    assert f"CONDA-RUN {allwell}" in r.stdout
    assert f"env {CELLPOSE_CONDA_ENV} is not installed" in r.stdout and "float32" in r.stdout
    assert "Bootstrap" in r.stdout


@_NEEDS_BASH
def test_a_host_with_neither_env_fails_with_the_guard_s_own_message(remote_home):  # noqa: F811
    _, binder = remote_home
    _stub_conda(binder, envs={"base": "/opt/conda"})
    r = _run(_conda_env_guard(_PREFER) + "; echo REACHED-PIPELINE")
    assert r.returncode == 127 and "REACHED-PIPELINE" not in r.stdout
    assert f"conda env {DEFAULT_CONDA_ENV} is not usable on" in r.stderr


@_NEEDS_BASH
def test_the_composed_command_with_the_cellpose_env_is_valid_shell():
    argv = ["conda", "run", "--no-capture-output", "-p", '"$AW_PREFIX"', "python", "pm.py"]
    for launcher in ("", "srun --partition=gpu"):
        cmd = _compose_remote_cmd(launcher, "/tmp/aw.pid", argv, cache_hit=False, env_selector=_PREFER)
        r = subprocess.run(["bash", "-n", "-c", cmd], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert '-p "$AW_PREFIX"' in cmd or "-p \"$AW_PREFIX\"" in cmd.replace("'\\''", "'")
