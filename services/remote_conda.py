"""How a command is built to run inside the remote's conda environment.

Extracted from ``services/remote_runner.py``, which re-exports every name so
no call site moved, because it is a self-contained cluster with one job and
the module it came from is the largest in the repository, under a line budget
that only moves down.

The job is one question asked in four places — the single-run launch, the
batched driver, the scheduler handoff and the pre-flight probe — and the
answer has to be identical in all four or a run starts under the wrong
interpreter and fails somewhere with no traceback. It is: given a host's
``remote_python``, which conda environment does the pipeline run in, is it
named or is it an absolute prefix, what ``conda run`` argv does that produce,
and what shell guard proves it exists before anything is launched into it.

Qt-free and ``review``-free, like everything under ``services/``. It depends
on ``remote_transport._remote_quote`` and nothing else in this package, which
is what lets it sit below ``remote_runner`` rather than beside it.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from services.remote_config import DEFAULT_CONDA_ENV, DEFAULT_REMOTE_PYTHON
from services.remote_transport import _remote_quote


# v5.2.13: bash snippet that locates conda.sh wherever conda is
# installed (``$HOME/miniforge3`` for bootstrap-managed installs OR
# whatever ``conda info --base`` reports for pre-existing system
# condas) and sources it. Mirrors the discovery logic in
# ``services/remote_bootstrap.py`` so the runner doesn't care which path
# bootstrap actually used.
#
# Why ``conda run --no-capture-output -n <env>`` rather than the old
# hard-coded ``host.remote_python``: on hosts where the bootstrap
# found a pre-existing system conda (HPC module, ``/opt/conda``,
# etc.) the env lives somewhere like ``/opt/conda/envs/allwell``, not
# under ``~/miniforge3``. The hard-coded path then exits 127 — the
# v5.2.12 user hit exactly this. ``conda run`` resolves the python
# from inside the env so it works regardless of conda's install
# location.
_CONDA_ACTIVATE_SNIPPET = (
    'CONDA_SH=""; '
    'if [ -f "$HOME/miniforge3/etc/profile.d/conda.sh" ]; then '
    'CONDA_SH="$HOME/miniforge3/etc/profile.d/conda.sh"; '
    'elif command -v conda >/dev/null 2>&1; then '
    '_base=$(conda info --base 2>/dev/null); '
    '[ -n "$_base" ] && [ -f "$_base/etc/profile.d/conda.sh" ] '
    '&& CONDA_SH="$_base/etc/profile.d/conda.sh"; '
    'fi; '
    'if [ -z "$CONDA_SH" ]; then '
    'echo "[remote] ERROR: cannot find conda.sh on remote" >&2; '
    'exit 127; '
    'fi; '
    '. "$CONDA_SH"'
)


def _conda_run_prefix(env_name: str = DEFAULT_CONDA_ENV) -> list[str]:
    """Argv prefix that runs the following command inside conda env
    ``env_name``. ``--no-capture-output`` makes stdout/stderr stream
    through unchanged so the existing progress parser keeps working."""
    return ["conda", "run", "--no-capture-output", "-n", env_name]


# How the pipeline's conda environment is named on the remote: either by env
# *name* (``conda run -n allwell``, resolved through conda's own envs dirs) or
# by absolute *prefix* (``conda run -p /path/to/env``).
EnvSelector = tuple[str, str]

DEFAULT_ENV_SELECTOR: EnvSelector = ("name", DEFAULT_CONDA_ENV)


def _conda_env_selector(remote_python: str) -> tuple[EnvSelector, str]:
    """Resolve a host's ``remote_python`` to a ``conda run`` selector.

    Returns the selector plus a warning to show the user (empty when there is
    nothing to say).

    The Remotes dialog offers a **Remote python** field, and the run path used
    to ignore it entirely — every run was hard-coded to ``conda run -n allwell``
    no matter what the host was configured with. A user who pointed that field
    at a different environment (which is the only reason to touch it) got their
    setting silently dropped and the run died on the remote with conda's own
    ``EnvironmentLocationNotFound: not a conda environment``, naming a path they
    had never configured. The field is honoured here instead.

    Left at its default the field is only a *placeholder*, not a fact — the
    bootstrap creates the env inside whichever conda it found, which on a host
    with a pre-existing system conda is not under ``~/miniforge3``. So the
    default keeps the name selector and lets conda resolve it, which is what
    makes a system-conda install work at all.

    A value that is not shaped like ``<env>/bin/python`` cannot name an
    environment; rather than guess, the name selector is kept and the caller is
    told the setting was not usable.
    """
    value = (remote_python or "").strip()
    if not value or value == DEFAULT_REMOTE_PYTHON:
        return DEFAULT_ENV_SELECTOR, ""
    path = PurePosixPath(value)
    if path.parent.name == "bin" and path.name.startswith("python"):
        return ("prefix", str(path.parent.parent)), ""
    return DEFAULT_ENV_SELECTOR, (
        f"configured Remote python {value!r} is not shaped like "
        f"'<env>/bin/python', so it cannot name a conda environment; "
        f"falling back to the env named {DEFAULT_CONDA_ENV!r}"
    )


def _conda_run_prefix_for(selector: EnvSelector) -> list[str]:
    """``conda run`` argv prefix for *selector* (``-n name`` or ``-p prefix``)."""
    kind, value = selector
    return ["conda", "run", "--no-capture-output",
            "-p" if kind == "prefix" else "-n", value]


# Shell helpers for locating and validating a conda env on the remote. Shared
# with ``services/remote_bootstrap.py`` so the runner's pre-launch check and the
# bootstrap's create-or-skip decision cannot disagree about whether an
# environment exists — the state where they *did* disagree is the one that
# stranded users (see :func:`_conda_env_guard`).
#
# Validity is conda's own test, ``is_conda_environment()``: a prefix is an
# environment iff it holds ``conda-meta/history``. A directory without that file
# is not an environment no matter what it contains, and conda refuses both to
# run in it and to create over it.
CONDA_ENV_SHELL_HELPERS = r"""
aw_env_prefix() {
    conda env list 2>/dev/null | awk -v n="$1" '$1 == n { print $NF }' | head -1
}
aw_env_is_valid() {
    [ -n "$1" ] && [ -f "$1/conda-meta/history" ]
}
aw_env_stale_dir() {
    for _c in "$HOME/.conda/envs/$1" "$(conda info --base 2>/dev/null)/envs/$1"; do
        if [ -d "$_c" ] && [ ! -f "$_c/conda-meta/history" ]; then
            printf '%s\n' "$_c"
            return 0
        fi
    done
    return 1
}
"""


def _conda_env_guard(selector: EnvSelector) -> str:
    """Bash that aborts with an actionable message if *selector* has no usable env.

    Without this the run reaches ``conda run`` and dies on conda's own
    ``EnvironmentLocationNotFound: not a conda environment: <path>`` — a line
    with no ``[remote]`` prefix, no host name, and no hint that the fix is to
    bootstrap the host. It is also indistinguishable, in a scrolling log, from
    an error the *pipeline* raised, which sends the diagnosis to the wrong
    machine entirely.

    Two states have to be told apart, because the fix differs and conda's own
    message conflates them:

    *No environment.* The host was never bootstrapped. Bootstrap it.

    *A directory that is not an environment.* Something exists at the prefix but
    holds no ``conda-meta/history`` — an env creation that failed part-way,
    which on a cluster is most often a full home-directory quota. This is the
    worse state: conda will neither run in it nor ``conda create`` over it, so
    the run fails *and* a plain re-bootstrap fails behind it. The message names
    the directory, because that is the thing that has to go.

    The check runs on the remote, immediately before the pipeline launches, so
    on a scheduler-backed host it runs **inside the allocation** — the compute
    node is where the env has to exist, and it is not always the login node's
    filesystem.
    """
    kind, value = selector
    value_q = _remote_quote(value)
    if kind == "prefix":
        # The prefix is given, so there is nothing to look up: it either is an
        # environment, is a directory that is not one, or is absent.
        resolve = (
            f'AW_PREFIX={value_q}; '
            f'AW_STALE=""; '
            f'if [ -d "$AW_PREFIX" ] && ! aw_env_is_valid "$AW_PREFIX"; then '
            f'AW_STALE="$AW_PREFIX"; fi'
        )
        what = f'conda env prefix {value}'
        # No double quotes anywhere in these strings: they are interpolated
        # into a double-quoted bash ``echo``, where a stray " ends the string
        # and the message arrives mangled (or the shell fails to parse it).
        missing_fix = ('Set this hosts Remote python to the env python, or run '
                       'Analyze -> Remotes -> Bootstrap to create the env.')
    else:
        resolve = (
            f'AW_PREFIX="$(aw_env_prefix {value_q})"; '
            f'AW_STALE="$(aw_env_stale_dir {value_q} || true)"'
        )
        what = f"conda env {value}"
        missing_fix = ('Run Analyze -> Remotes -> Bootstrap for this host to '
                       'create it, or set Remote python to an existing env '
                       'python.')
    # The guard never deletes: a run is not the time to modify the host, and
    # what is inside that directory is unreadable to us. Print the one command
    # that unblocks it and let the user decide.
    stale_fix = ('ACTION REQUIRED: remove that directory yourself, then re-run '
                 'Analyze -> Remotes -> Bootstrap to rebuild the env:')
    return (
        f'{CONDA_ENV_SHELL_HELPERS} '
        f'{resolve}; '
        f'if ! aw_env_is_valid "$AW_PREFIX"; then '
        f'echo "[remote] ERROR: {what} is not usable on $(hostname)." >&2; '
        f'if [ -n "$AW_STALE" ]; then '
        f'echo "[remote] $AW_STALE exists but is not a conda environment '
        f'(no conda-meta/history)." >&2; '
        f'echo "[remote] An env creation failed part-way — on a cluster a full '
        f'home-directory quota is the usual cause. conda will neither run in '
        f'it nor create over it." >&2; '
        f'echo "[remote] {stale_fix}" >&2; '
        f'echo "[remote]     rm -rf $AW_STALE" >&2; '
        f'echo "[remote] Check the quota first (quota -s, or du -sh \\$HOME) — '
        f'if that is what stopped the install, it will stop the next one too." '
        f'>&2; '
        f'else '
        f'echo "[remote] {missing_fix}" >&2; '
        f'fi; '
        f'echo "[remote] conda base: $(conda info --base 2>/dev/null || '
        f'echo unknown)" >&2; '
        f'echo "[remote] environments conda can see:" >&2; '
        # Prefix conda's own listing too. Every line of this block has to be
        # attributable to the remote at a glance — an unprefixed listing in the
        # middle of a labelled block reads like local output, which is the
        # confusion the whole guard exists to remove.
        f"conda env list 2>/dev/null | sed 's/^/[remote]   /' >&2 || true; "
        f'exit 127; '
        f'fi'
    )
