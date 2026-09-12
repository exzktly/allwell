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


# ── the frozen windowed build's nulled standard streams ─────────────────────
#
# A frozen ``console=False`` build leaves ``sys.stdout`` / ``sys.stderr`` — and
# ``sys.__stdout__`` / ``sys.__stderr__`` — set to None, even though the parent
# attached a live pipe to fd 1/2 and is reading it. ``process_microscopy`` and
# ``well_plate_zipper`` each repair the pair from their own ``main()`` via
# ``util.frozen_io.ensure_std_streams``; the bootstrap is the third subprocess
# entry point and was the one that did not, so the very first ``say()`` raised
# ``AttributeError: 'NoneType' object has no attribute 'isatty'`` and the
# Remotes dialog showed an empty log for a process that died before it started.


def _bootstrap_module():
    """Load ``scripts/_bootstrap_lib.py`` the way the launcher does."""
    import importlib.util

    path = REPO_ROOT / "scripts" / "_bootstrap_lib.py"
    spec = importlib.util.spec_from_file_location("_bootstrap_lib_probe", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_bootstrap_lib_probe"] = module
    spec.loader.exec_module(module)
    return module


def test_say_survives_a_nulled_stdout(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    _bootstrap_module().say("provisioning host")   # must not raise


def test_the_tee_survives_a_nulled_primary():
    """The log half must keep working when there is no stream to mirror to."""
    import io

    log = io.StringIO()
    tee = _bootstrap_module()._TeeStream(None, log)
    tee.write("provisioning\n")
    tee.flush()
    assert log.getvalue() == "provisioning\n"
    assert tee.isatty() is False


def test_the_tee_still_reaches_a_live_primary():
    import io

    primary, log = io.StringIO(), io.StringIO()
    tee = _bootstrap_module()._TeeStream(primary, log)
    tee.write("x\n")
    assert primary.getvalue() == "x\n"
    assert log.getvalue() == "x\n"


def test_the_launcher_repairs_the_streams_before_loading_the_module():
    """Without this the repair never runs for the bootstrap: it is reached
    through importlib, not through a ``main()`` of its own that could call
    ``ensure_std_streams`` the way the pipeline and zipper entry points do."""
    src = (REPO_ROOT / "all_well_launcher.py").read_text(encoding="utf-8")
    branch = src.split('"--run-bootstrap"', 1)[1]
    assert "ensure_std_streams" in branch.split("def ", 1)[0], (
        "all_well_launcher's --run-bootstrap branch must call "
        "util.frozen_io.ensure_std_streams before loading _bootstrap_lib"
    )


def test_the_tee_wraps_the_repaired_streams_not_the_dunder_pair():
    """``ensure_std_streams`` repairs ``sys.stdout`` / ``sys.stderr``; it does
    not touch ``sys.__stdout__`` / ``sys.__stderr__``, which stay None for the
    life of a frozen windowed process. Teeing the dunder pair therefore wraps
    None and every later write raises."""
    import ast

    src = (REPO_ROOT / "scripts" / "_bootstrap_lib.py").read_text(encoding="utf-8")
    primaries = [
        ast.unparse(node.args[0])
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_TeeStream"
        and node.args
    ]
    assert primaries, "no _TeeStream construction found"
    assert set(primaries) == {"sys.stdout", "sys.stderr"}, (
        f"the bootstrap log tee wraps {sorted(set(primaries))}; a frozen "
        "windowed build nulls sys.__stdout__ / sys.__stderr__ permanently, so "
        "it must wrap the live (repaired) sys.stdout / sys.stderr instead"
    )
