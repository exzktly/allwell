"""The remote run resolves — and checks — the conda environment it will use.

Two failures motivate this file, and they look identical in the run log:

* The Remotes dialog offers a **Remote python** field that the run path used to
  ignore outright. Every run was hard-coded to ``conda run -n allwell``, so a
  user who pointed the field at a different environment had the setting
  silently dropped and the run died on the remote with conda's own
  ``EnvironmentLocationNotFound: not a conda environment``, naming a path they
  never configured.
* A host that was never bootstrapped has no ``allwell`` env at all, and failed
  the same way.

Neither line carried a ``[remote]`` prefix, a host name, or any hint that the
fix lives in the Remotes dialog — and in a scrolling log a bare conda error is
indistinguishable from one the *pipeline* raised, which sends the diagnosis to
the wrong machine.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from services.remote_config import (
    DEFAULT_CONDA_ENV,
    DEFAULT_REMOTE_PYTHON,
    RemoteHost,
)
from services.remote_runner import (
    _CONDA_ACTIVATE_SNIPPET,
    LocalInputMode,
    OutputFilter,
    RemoteRunner,
    _compose_remote_cmd,
    _conda_env_guard,
    _conda_env_selector,
    _conda_run_prefix_for,
)

_PIDFILE = "~/.allwell-staging/runs/r1.pid"
_ARGV = ["conda", "run", "--no-capture-output", "-n", "allwell", "python", "pm.py"]


# ── which environment ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["", "   ", DEFAULT_REMOTE_PYTHON])
def test_unset_remote_python_keeps_the_env_name(value):
    """The default is a placeholder, not a fact.

    The bootstrap creates the env inside whichever conda it found — on a host
    with a pre-existing system conda that is nowhere near ``~/miniforge3`` — and
    it writes the placeholder to ``remotes.toml`` regardless. Trusting it as a
    path would break exactly the system-conda hosts ``conda run -n`` was
    introduced to fix.
    """
    selector, warning = _conda_env_selector(value)
    assert selector == ("name", DEFAULT_CONDA_ENV)
    assert warning == ""


def test_a_configured_remote_python_selects_its_env_prefix():
    selector, warning = _conda_env_selector("/opt/conda/envs/myenv/bin/python")
    assert selector == ("prefix", "/opt/conda/envs/myenv")
    assert warning == ""


def test_a_tilde_remote_python_keeps_the_tilde_for_the_remote_shell():
    selector, _ = _conda_env_selector("~/mamba/envs/aw/bin/python3.10")
    assert selector == ("prefix", "~/mamba/envs/aw")


def test_a_remote_python_that_cannot_name_an_env_warns_instead_of_guessing():
    selector, warning = _conda_env_selector("python3")
    assert selector == ("name", DEFAULT_CONDA_ENV)
    assert "python3" in warning
    assert "<env>/bin/python" in warning


def test_run_prefix_uses_p_for_a_prefix_and_n_for_a_name():
    assert _conda_run_prefix_for(("prefix", "/opt/conda/envs/x")) == [
        "conda", "run", "--no-capture-output", "-p", "/opt/conda/envs/x"]
    assert _conda_run_prefix_for(("name", "allwell")) == [
        "conda", "run", "--no-capture-output", "-n", "allwell"]


def test_the_pipeline_argv_honours_the_hosts_remote_python():
    """The regression itself: a configured env reaches the actual command."""
    host = RemoteHost(
        name="h", ssh_alias="h",
        remote_python="/opt/conda/envs/myenv/bin/python",
    )
    argv = RemoteRunner(host)._build_remote_argv(
        ["python", "pm.py", "--input_dir", "/in", "--output_dir", "/out"],
        "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP,
    )
    assert argv[:5] == [
        "conda", "run", "--no-capture-output", "-p", "/opt/conda/envs/myenv"]


def test_an_unconfigured_host_still_runs_by_env_name():
    host = RemoteHost(name="h", ssh_alias="h")
    assert RemoteRunner(host).env_selector == ("name", DEFAULT_CONDA_ENV)


# ── the pre-launch check ────────────────────────────────────────────────────

def test_the_guard_runs_after_conda_is_sourced_and_before_the_pipeline():
    cmd = _compose_remote_cmd("", _PIDFILE, _ARGV, cache_hit=False)
    assert cmd.index(_CONDA_ACTIVATE_SNIPPET) < cmd.index("conda env list")
    assert cmd.index("conda env list") < cmd.index("pm.py")


def test_the_guard_runs_inside_the_scheduler_allocation():
    """On a scheduler-backed host the compute node is where the env has to
    exist — it is not always the login node's filesystem — so the check has to
    be inside the ``srun`` step, not before it."""
    cmd = _compose_remote_cmd(
        "srun --partition=gpu", _PIDFILE, _ARGV, cache_hit=False)
    inner = cmd.split("bash -lc ", 1)[1]
    assert "conda env list" in inner
    assert inner.index("conda env list") < inner.index("pm.py")


def test_a_cache_hit_skips_the_check():
    """A cache hit execs ``true`` and imports nothing, so failing it on a
    missing env would break a run that never needed one."""
    cmd = _compose_remote_cmd("", _PIDFILE, ["true"], cache_hit=True)
    assert "conda env list" not in cmd
    assert cmd.endswith("exec true")


def test_the_guard_checks_the_configured_prefix_not_the_default_name():
    cmd = _compose_remote_cmd(
        "", _PIDFILE, _ARGV, cache_hit=False,
        env_selector=("prefix", "/opt/conda/envs/myenv"),
    )
    assert "[ -x /opt/conda/envs/myenv/bin/python ]" in cmd
    assert "grep -qx allwell" not in cmd


# ── the message the user actually reads ─────────────────────────────────────

_NEEDS_BASH = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs a POSIX shell to run the guard")


_FAKE_CONDA = """#!/bin/sh
case "$1 $2" in
  "info --base") echo /opt/conda ;;
  "env list")
    echo "# conda environments:"
    echo "#"
    echo "base                  *  /opt/conda"
    ;;
esac
"""


@pytest.fixture
def conda_on_path(tmp_path, monkeypatch):
    """A stub ``conda`` that answers the two queries the guard makes.

    The guard's output depends on what conda reports, so the test has to own
    that side. Reading whatever conda the machine happens to have made this
    pass locally (no conda installed → empty listing) and fail on CI (a real
    conda → a real listing), which says nothing about the code.
    """
    binder = tmp_path / "bin"
    binder.mkdir()
    conda = binder / "conda"
    conda.write_text(_FAKE_CONDA)
    conda.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binder}:/usr/bin:/bin")
    return binder


def _run_guard(selector) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", _conda_env_guard(selector) + "; echo REACHED-PIPELINE"],
        capture_output=True, text=True,
    )


@_NEEDS_BASH
@pytest.mark.parametrize("selector", [
    ("name", "allwell"),
    ("prefix", "/definitely/not/an/env"),
])
def test_a_missing_env_aborts_with_a_labelled_actionable_message(
    selector, conda_on_path,
):
    r = _run_guard(selector)
    assert r.returncode == 127
    assert "REACHED-PIPELINE" not in r.stdout
    # Every line the user reads is attributed to the remote — including the
    # environment listing, which is conda's own output.
    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
    assert lines and all(ln.startswith("[remote]") for ln in lines), r.stderr
    # …names what is missing, what conda knows, and how to fix it.
    assert "not found on" in r.stderr
    assert "conda base: /opt/conda" in r.stderr
    assert "[remote]   base" in r.stderr
    assert "Bootstrap" in r.stderr


@_NEEDS_BASH
def test_an_env_that_exists_passes_through_to_the_pipeline(tmp_path, conda_on_path):
    env = tmp_path / "envs" / "aw"
    (env / "bin").mkdir(parents=True)
    py = env / "bin" / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)

    r = _run_guard(("prefix", str(env)))
    assert r.returncode == 0
    assert "REACHED-PIPELINE" in r.stdout


@_NEEDS_BASH
def test_a_named_env_conda_lists_passes_through_to_the_pipeline(
    tmp_path, monkeypatch,
):
    """The name selector's happy path: the guard's check has to agree with the
    resolution ``conda run -n`` will do a moment later, or it would block runs
    that would have worked."""
    binder = tmp_path / "bin"
    binder.mkdir()
    conda = binder / "conda"
    conda.write_text(
        '#!/bin/sh\n'
        '[ "$1 $2" = "env list" ] && printf "%s\\n" '
        '"# conda environments:" "base   /opt/conda" "allwell   /opt/conda/envs/allwell"\n'
    )
    conda.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binder}:/usr/bin:/bin")

    r = _run_guard(("name", "allwell"))
    assert r.returncode == 0
    assert "REACHED-PIPELINE" in r.stdout


@_NEEDS_BASH
@pytest.mark.parametrize("selector", [
    ("name", "allwell"),
    ("prefix", "/opt/conda/envs/myenv"),
    ("prefix", "~/miniforge3/envs/allwell"),
])
def test_the_composed_command_is_valid_shell(selector):
    """The guard is interpolated into a double-quoted ``echo``; a stray quote
    in its wording would end the string and mangle — or fail to parse — the
    whole remote command."""
    for launcher in ("", "srun --partition=gpu"):
        cmd = _compose_remote_cmd(
            launcher, _PIDFILE, _ARGV, cache_hit=False, env_selector=selector)
        r = subprocess.run(["bash", "-n", "-c", cmd], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
