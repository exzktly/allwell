"""Guardrail: the WellPlateZipper subprocess must use the frozen dispatch.

In a frozen (PyInstaller) bundle ``sys.executable`` is the .app binary, not a
Python interpreter, so ``[sys.executable, "WellPlateZipper.py", ...]`` would
just relaunch the GUI and the grouping phase would never run — the analysis
pipeline fails when built as a binary. ``build_zipper_cmd`` must instead emit
the ``--run-zipper`` sentinel (which all_well_launcher routes into
WellPlateZipper.main()), mirroring the pipeline's ``--run-pipeline`` path.

This is a frozen-only failure mode that a normal (unfrozen) CI run of the app
can't reproduce, so it's pinned here at the command-build layer.
"""

import sys
from pathlib import Path

from services.input_resolution_service import build_zipper_cmd

_ZIP = Path("/bundle/WellPlateZipper.py")
_TIF = Path("/data/tifs")
_OUT = Path("/data/in")


def _cmd(**kw):
    return build_zipper_cmd(_ZIP, _TIF, _OUT, "well:field:z:c", "_", **kw)


def test_source_mode_invokes_the_script_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cmd = _cmd()
    assert cmd[0] == sys.executable
    assert cmd[1] == str(_ZIP)          # runs python on the .py file
    assert "--run-zipper" not in cmd


def test_frozen_mode_uses_run_zipper_sentinel(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = _cmd()
    # Sentinel must be argv[1] so the launcher's ``argv[1] == "--run-zipper"``
    # dispatch fires, and the .py path must NOT be passed (it would relaunch
    # the GUI in a real bundle).
    assert cmd[:2] == [sys.executable, "--run-zipper"]
    assert str(_ZIP) not in cmd
    assert not any(str(a).endswith("WellPlateZipper.py") for a in cmd)


def test_args_are_forwarded_in_both_modes(monkeypatch):
    for frozen in (False, True):
        monkeypatch.setattr(sys, "frozen", frozen, raising=False)
        cmd = _cmd(move_source_tifs=True)
        assert "--search-dir" in cmd and str(_TIF) in cmd
        assert "--output-dir" in cmd and str(_OUT) in cmd
        assert cmd[cmd.index("--filename_schema") + 1] == "well:field:z:c"
        assert cmd[cmd.index("--filename_sep") + 1] == "_"
        assert "--move-source-tifs" in cmd
