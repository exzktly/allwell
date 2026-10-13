"""A re-bootstrap installs whatever the remote env is missing, not only TensorFlow.

``install_pip_deps`` used to skip the whole ``pip install -r
requirements-remote.txt`` as soon as ``tensorflow`` imported in the env. An env
built before Cellpose joined the requirements — or one whose Torch half failed
to install — therefore reported ready on every later bootstrap, and the run died
on the compute node at ``from cellpose import models`` with "Cellpose
segmentation requires the 'cellpose' package".

The probe now asks the env for every module in ``REQUIRED_IMPORTS`` and the
install runs whenever any is missing. These tests hold three things: the roster
covers the requirements file, the probe's own shell line reports the right
modules when actually run, and the install decision follows the probe.
"""

from __future__ import annotations

import re
import subprocess
import types
from pathlib import Path

from services import remote_bootstrap as rb

_REQ = Path(__file__).resolve().parents[1] / "requirements-remote.txt"


def _requirement_names() -> set[str]:
    names = set()
    for raw in _REQ.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.add(re.split(r"[\s=<>!~;\[]", line, maxsplit=1)[0].lower())
    return names


def test_every_requirement_is_probed_or_exempt_with_a_reason():
    names = _requirement_names()
    covered = set(rb.REQUIRED_IMPORTS) | set(rb.NOT_PROBED)
    assert names - covered == set(), (
        "requirements-remote.txt installs packages the bootstrap probe never "
        "looks for, so an env missing them reports ready and skips the "
        f"install: {sorted(names - covered)}. Add each to REQUIRED_IMPORTS (or "
        "to NOT_PROBED with the reason).")
    assert covered - names == set(), (
        f"REQUIRED_IMPORTS / NOT_PROBED name packages the requirements file no "
        f"longer installs: {sorted(covered - names)}")
    assert all(reason.strip() for reason in rb.NOT_PROBED.values())


def _probe_line() -> str:
    lines = [ln.strip() for ln in rb._PROBE_SCRIPT.splitlines()
             if "MISSING_MODULES=$(" in ln]
    assert len(lines) == 1, "the probe should compute the missing set in one line"
    return lines[0]


def test_the_probe_line_reports_what_the_env_cannot_find(tmp_path):
    """The shell line the remote runs, run here under bash with a real python.

    A top-level miss, a dotted name under a missing parent (``find_spec``
    raises on that unless the parent is checked first) and two modules that are
    present, so a probe that reports everything or nothing fails too.
    """
    import sys

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").symlink_to(sys.executable)
    roster = " ".join(rb.REQUIRED_IMPORTS.values())
    line = _probe_line()
    assert roster in line, "the probe line no longer carries the roster"
    cmd = (line.replace(roster, "re json no_such_pkg_aw nopkg_aw.sub")
           + '; echo "missing_modules=$MISSING_MODULES"')
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                         env={"PATH": f"{bindir}:/usr/bin:/bin"}, check=True)
    assert out.stdout.strip() == "missing_modules=no_such_pkg_aw,nopkg_aw.sub"


def test_the_probe_parses_the_missing_set(monkeypatch):
    stdout = ("has_conda=true\nhas_repo=true\nhas_env=true\n"
              "tensorflow_version=2.13.0\nstardist_ok=true\ngpu_visible=false\n"
              "python_version=3.10.14\nmissing_modules=torch,cellpose\n"
              "pip_in_progress=false\n")
    monkeypatch.setattr(rb, "_ssh_run",
                        lambda *a, **k: types.SimpleNamespace(stdout=stdout))
    probe = rb.probe_host("h")
    assert probe.missing_modules == ("torch", "cellpose")


def _probe(**kw) -> rb.HostProbe:
    base = dict(has_conda=True, has_repo=True, has_env=True,
                tensorflow_version="2.13.0", stardist_ok=True,
                gpu_visible=False, python_version="3.10.14")
    base.update(kw)
    return rb.HostProbe(**base)


def _install_calls(monkeypatch, probe: rb.HostProbe) -> tuple[list, list]:
    calls: list = []
    said: list[str] = []
    monkeypatch.setattr(rb, "_ssh_run", lambda *a, **k: calls.append((a, k))
                        or types.SimpleNamespace(stdout="", returncode=0))
    monkeypatch.setattr(rb, "say", said.append)
    rb.install_pip_deps("h", probe)
    return calls, said


def test_an_env_with_tensorflow_but_no_cellpose_is_installed_into(monkeypatch):
    """The reported case: TensorFlow imports, Cellpose and Torch do not."""
    calls, said = _install_calls(
        monkeypatch, _probe(missing_modules=("torch", "cellpose")))
    assert calls, "the install was skipped although cellpose is missing"
    assert "requirements-remote.txt" in str(calls[-1])
    assert any("missing torch, cellpose" in s for s in said)


def test_an_env_the_probe_could_not_read_is_installed_into(monkeypatch):
    calls, _ = _install_calls(monkeypatch, _probe(missing_modules=("?",)))
    assert calls


def test_a_complete_env_is_left_alone(monkeypatch):
    calls, said = _install_calls(monkeypatch, _probe())
    assert calls == []
    assert any("skipping pip install" in s for s in said)
