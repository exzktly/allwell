"""A remote env is ready when it can import what the pipeline imports.

The pre-launch guard (``services.remote_conda._conda_env_guard``) used to ask
one question of the ``allwell`` env: does ``conda-meta/history`` exist? The
bootstrap creates the env and then pip-installs into it, detached; that install
can fail, still be running, or leave a module that will not import, and in each
case the guard passed and the run died on ``ModuleNotFoundError`` after its
queue wait and its upload (remote review S7). The Cellpose env had a
readiness probe, but it asked ``importlib.util.find_spec``, which finds an
installed-but-broken module — a compiled extension built against another numpy,
the ``imagecodecs`` case that has happened — and so did the bootstrap's own
probe, which then skipped the install that might have repaired it (H1).

Each test runs the real guard under bash, against a stub ``conda`` and an env
whose ``python`` is the real interpreter with a directory of stand-in modules
first on its path, so "importable", "present but broken" and "absent" are each
real outcomes of a real ``import``.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from services import remote_bootstrap as rb
from services.remote_conda import (
    CELLPOSE_CONDA_ENV,
    CELLPOSE_IMPORTS,
    PIPELINE_IMPORTS,
    _conda_env_guard,
)
from tests._repo_scan import REPO_ROOT, parse_source

pytestmark = pytest.mark.skipif(shutil.which("bash") is None,
                                reason="needs a POSIX shell to run the guard")

_PREFER = ("prefer", f"{CELLPOSE_CONDA_ENV} allwell")
_BROKEN = 'raise ImportError("numpy.core.multiarray failed to import")\n'


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A scratch HOME, a stub conda on PATH, and a factory for envs."""
    home = tmp_path / "home"
    (home / ".allwell-staging").mkdir(parents=True)
    binder = tmp_path / "bin"
    binder.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{binder}:/usr/bin:/bin")
    envs: dict[str, str] = {}

    def make_env(name: str, *, broken: tuple[str, ...] = (),
                 modules: tuple[str, ...] = PIPELINE_IMPORTS + CELLPOSE_IMPORTS) -> Path:
        prefix = tmp_path / "envs" / name
        stubs = prefix / "stubs"
        stubs.mkdir(parents=True)
        for m in modules:
            (stubs / f"{m}.py").write_text(_BROKEN if m in broken else "")
        (prefix / "conda-meta").mkdir()
        (prefix / "conda-meta" / "history").touch()
        (prefix / "bin").mkdir()
        py = prefix / "bin" / "python"
        py.write_text(f'#!/bin/sh\nPYTHONPATH="{stubs}" exec "{sys.executable}" "$@"\n')
        py.chmod(0o755)
        envs[name] = str(prefix)
        rows = "\n".join(f'  echo "{n}   {p}"' for n, p in envs.items())
        (binder / "conda").write_text(
            "#!/bin/sh\n"
            'case "$1 $2" in\n'
            '  "info --base") echo /opt/conda ;;\n'
            f'  "env list") echo "# conda environments:"\n{rows} ;;\n'
            "esac\n")
        (binder / "conda").chmod(0o755)
        return prefix

    return types.SimpleNamespace(home=home, staging=home / ".allwell-staging", make_env=make_env)


def _guard(selector) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", _conda_env_guard(selector) + '; echo "REACHED-PIPELINE in $AW_PREFIX"'],
        capture_output=True, text=True, timeout=120)


def test_a_ready_env_passes(remote):
    env = remote.make_env("allwell")
    r = _guard(("name", "allwell"))
    assert r.returncode == 0, r.stderr
    assert f"REACHED-PIPELINE in {env}" in r.stdout


def test_an_env_whose_last_install_failed_is_refused(remote):
    """S7: every module imports — the half-install left some — but the
    bootstrap recorded that its pip install failed."""
    remote.make_env("allwell")
    (remote.staging / ".pip_failed").touch()
    r = _guard(("name", "allwell"))
    assert r.returncode == 127
    assert "REACHED-PIPELINE" not in r.stdout
    assert ("[remote] ERROR: conda env allwell on " in r.stderr
            and "is not ready to run the pipeline: the last pip install into it failed "
                "(its log is ~/.allwell-staging/.pip_install.log)." in r.stderr), r.stderr
    assert "Remotes -> Bootstrap" in r.stderr


def test_an_env_still_being_installed_is_refused(remote):
    remote.make_env("allwell")
    (remote.staging / ".pip_running").write_text(str(os.getpid()))   # a live pid
    r = _guard(("name", "allwell"))
    assert r.returncode == 127 and "REACHED-PIPELINE" not in r.stdout
    assert "a pip install into it is still running" in r.stderr


def test_an_env_with_a_module_that_will_not_import_is_refused(remote):
    """H1: ``imagecodecs`` is installed — ``find_spec`` finds it — and raises
    on import. The guard names the module and the error, and says what to do
    when Bootstrap cannot repair it."""
    remote.make_env("allwell", broken=("imagecodecs",))
    r = _guard(("name", "allwell"))
    assert r.returncode == 127 and "REACHED-PIPELINE" not in r.stdout
    assert ("its python cannot import what the pipeline needs "
            "(imagecodecs: ImportError: numpy.core.multiarray failed to import)") in r.stderr
    assert "conda env remove -n allwell" in r.stderr


def test_the_bootstrap_markers_are_read_only_for_the_env_bootstrap_manages(remote):
    """A Remote python pointing at the user's own env is judged on its imports:
    a failed install of ``allwell`` says nothing about it."""
    env = remote.make_env("mine")
    (remote.staging / ".pip_failed").touch()
    r = _guard(("prefix", str(env)))
    assert r.returncode == 0, r.stderr
    assert f"REACHED-PIPELINE in {env}" in r.stdout


def test_a_cellpose_env_with_a_broken_cellpose_is_not_chosen(remote):
    """H1 for the Cellpose env: ``cellpose`` is found and does not import, so a
    Cellpose run goes to ``allwell`` (float32), and the note says why."""
    allwell = remote.make_env("allwell")
    remote.make_env(CELLPOSE_CONDA_ENV, broken=("cellpose",))
    r = _guard(_PREFER)
    assert r.returncode == 0, r.stderr
    assert f"REACHED-PIPELINE in {allwell}" in r.stdout
    assert "cellpose: ImportError: numpy.core.multiarray failed to import" in r.stdout


def test_a_ready_cellpose_env_is_chosen_without_probing_allwell_again(remote):
    remote.make_env("allwell", broken=("numpy",))   # would refuse, if asked
    cellpose = remote.make_env(CELLPOSE_CONDA_ENV)
    r = _guard(_PREFER)
    assert r.returncode == 0, r.stderr
    assert f"REACHED-PIPELINE in {cellpose}" in r.stdout


# ── the roster is what the pipeline really imports ──────────────────────────

_FIRST_PARTY = {"pipeline", "util", "services", "process_microscopy", "domain", "core"}


def _module_scope_third_party(module: str) -> dict[str, str]:
    """``{top-level package: the first-party module importing it}`` for every
    third-party import made at module scope by *module* and, transitively, by
    every first-party module it imports at module scope."""
    found: dict[str, str] = {}
    seen: set[str] = set()

    def path_of(name: str) -> Path | None:
        for cand in (REPO_ROOT / (name.replace(".", "/") + ".py"),
                     REPO_ROOT / name.replace(".", "/") / "__init__.py"):
            if cand.is_file():
                return cand
        return None

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        path = path_of(name)
        if path is None:
            return
        for stmt in parse_source(path).body:
            nodes = ast.walk(stmt) if isinstance(stmt, (ast.If, ast.Try)) else [stmt]
            for node in nodes:
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
                else:
                    continue
                for imported in names:
                    top = imported.split(".")[0]
                    if top in _FIRST_PARTY:
                        visit(imported)
                    elif top not in sys.stdlib_module_names and top != "__future__":
                        found.setdefault(top, name)

    visit(module)
    return found


def test_the_guard_imports_everything_the_pipeline_imports_at_module_scope():
    needed = _module_scope_third_party("process_microscopy")
    assert needed, "the scan found no third-party import at all"
    missing = {m: by for m, by in needed.items() if m not in PIPELINE_IMPORTS}
    assert missing == {}, (
        "the pipeline imports these at module scope, so an env without them "
        "cannot start a run, but the env guard does not check them: "
        f"{missing}. Add each to remote_conda.PIPELINE_IMPORTS.")


# ── the bootstrap's own probe ───────────────────────────────────────────────

def test_the_bootstrap_probe_reports_a_module_that_is_found_but_will_not_import(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "aw_broken_mod.py").write_text(_BROKEN)
    (stubs / "aw_fine_mod.py").write_text("")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").symlink_to(sys.executable)
    (line,) = [ln.strip() for ln in rb._PROBE_SCRIPT.splitlines() if "MISSING_MODULES=$(" in ln]
    roster = " ".join(rb.REQUIRED_IMPORTS.values())
    cmd = (line.replace(roster, "aw_fine_mod aw_broken_mod json")
           + '; echo "missing_modules=$MISSING_MODULES"')
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=120,
                         env={"PATH": f"{bindir}:/usr/bin:/bin", "PYTHONPATH": str(stubs)},
                         check=True)
    assert out.stdout.strip() == "missing_modules=aw_broken_mod"


def _probe(**kw) -> rb.HostProbe:
    base = dict(has_conda=True, has_repo=True, has_env=True, tensorflow_version="2.13.0",
                stardist_ok=True, gpu_visible=False, python_version="3.10.14")
    base.update(kw)
    return rb.HostProbe(**base)


def test_a_bootstrap_after_a_failed_install_installs_again(monkeypatch):
    """The guard's advice is to Bootstrap again; that has to re-run the failed
    install rather than skip it because every module happens to import — the
    failed marker is otherwise never cleared and every run stays refused."""
    calls: list = []
    monkeypatch.setattr(rb, "_ssh_run", lambda *a, **k: calls.append(k.get("input", ""))
                        or types.SimpleNamespace(stdout="", returncode=0))
    monkeypatch.setattr(rb, "say", lambda *_: None)
    rb.install_pip_deps("h", _probe(pip_failed=True))
    rb.install_cellpose_deps("h", _probe(cellpose_env=True, cellpose_pip_failed=True))
    assert len(calls) == 2 and "requirements-remote.txt" in calls[0]
    assert rb.CELLPOSE_REQUIREMENTS in calls[1]


def test_the_probe_reads_the_failed_markers(monkeypatch):
    stdout = ("has_conda=true\nhas_repo=true\nhas_env=true\ntensorflow_version=2.13.0\n"
              "pip_failed=true\ncellpose_pip_failed=false\n")
    monkeypatch.setattr(rb, "_ssh_run", lambda *a, **k: types.SimpleNamespace(stdout=stdout))
    probe = rb.probe_host("h")
    assert (probe.pip_failed, probe.cellpose_pip_failed) == (True, False)
    assert 'echo "pip_failed=' in rb._PROBE_SCRIPT and ".pip_failed" in rb._PROBE_SCRIPT
