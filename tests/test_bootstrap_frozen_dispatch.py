"""Guardrail: the remote-bootstrap subprocess must use the frozen dispatch.

In a frozen (PyInstaller) bundle ``sys.executable`` is the .app binary, not a
Python interpreter, so ``[sys.executable, "scripts/_bootstrap_lib.py", ...]``
would just relaunch the GUI and the remote provisioning would never run — the
user reported "bootstrap_lib was not found" launching the built app.
``build_bootstrap_cmd`` must instead emit the ``--run-bootstrap`` sentinel
(which all_well_launcher routes into ``scripts/_bootstrap_lib.main()``),
mirroring the pipeline's ``--run-pipeline`` and zipper's ``--run-zipper`` paths.

This is a frozen-only failure mode that a normal (unfrozen) CI run of the app
can't reproduce, so it's pinned here at the command-build layer, plus a static
check that the launcher and spec carry the matching dispatch + bundled file.
"""

import sys
from pathlib import Path

from services.input_resolution_service import build_bootstrap_cmd

REPO_ROOT = Path(__file__).resolve().parent.parent
_BOOT = Path("/bundle/scripts/_bootstrap_lib.py")


def test_source_mode_invokes_the_script_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cmd = build_bootstrap_cmd(_BOOT, "myhost")
    assert cmd == [sys.executable, str(_BOOT), "myhost"]
    assert "--run-bootstrap" not in cmd


def test_frozen_mode_uses_run_bootstrap_sentinel(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = build_bootstrap_cmd(_BOOT, "myhost")
    # Sentinel must be argv[1] so the launcher's ``argv[1] == "--run-bootstrap"``
    # dispatch fires, and the .py path must NOT be passed (it would relaunch the
    # GUI in a real bundle).
    assert cmd == [sys.executable, "--run-bootstrap", "myhost"]
    assert str(_BOOT) not in cmd
    assert not any(str(a).endswith("_bootstrap_lib.py") for a in cmd)


def test_launcher_dispatches_the_sentinel():
    """all_well_launcher must route ``--run-bootstrap`` into the bootstrap
    module — otherwise the sentinel falls through to the GUI."""
    src = (REPO_ROOT / "all_well_launcher.py").read_text()
    assert '"--run-bootstrap"' in src
    assert "_bootstrap_lib" in src


def test_spec_bundles_the_bootstrap_module():
    """The frozen build must ship ``scripts/_bootstrap_lib.py`` under a
    ``scripts/`` dest so its ``REPO_ROOT = __file__.parent.parent`` resolves to
    _MEIPASS (where the files it syncs to the remote are bundled)."""
    spec = (REPO_ROOT / "_Docs" / "_Installation" / "all_well.spec").read_text()
    assert '"scripts" / "_bootstrap_lib.py"' in spec
