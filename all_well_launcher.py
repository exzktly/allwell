"""
all_well_launcher.py
--------------------
PyInstaller entry point for AllWell.app.

Referenced by _Installation/all_well.spec as the entry point for the
macOS bundle. When the app is launched, PyInstaller runs this module
instead of allwell.py directly.

This launcher ensures:
  * matplotlib backend is set to QtAgg before any pyplot imports
  * bundled sibling modules are discoverable from the PyInstaller
    extracted resource directory (_MEIPASS)
  * allwell.main() is invoked without runtime patching of __name__
  * when re-invoked with ``--run-pipeline`` as the first argument,
    the launcher dispatches to ``process_microscopy.main()``
    instead of the GUI. The Analyze tab uses that path to spawn the
    pipeline subprocess from inside the frozen bundle, where
    ``sys.executable`` points at the .app binary and not a Python
    interpreter. ``--run-report`` dispatches the same way to the headless
    report (``allwell_report.main``), which from source is
    ``python allwell_report.py``.

When running from source (not bundled), sys._MEIPASS is not set, so
modules are loaded from the repository root instead.

**No test imports this module, and none should.** Importing it installs a
``setuptools._distutils.compilers`` runtime stub and runs the ``_MEIPASS``
bootstrap, both of which only mean anything inside a frozen bundle; doing that
in a test process would reshape the interpreter every later test runs in. It is
therefore the one entry in the zero-coverage inventory that is *meant* to have
no executed lines, rather than a module waiting for a test. What guards it
instead is source-level: ``tests/test_bootstrap_frozen_dispatch.py`` and
``tests/test_profile_meipass.py`` read this file through ``tests/_repo_scan``
and assert on its syntax tree, so the dispatch sentinel and the ``_MEIPASS``
path handling are pinned without the import ever happening.
"""

import sys

# ---------------------------------------------------------------------------
# Runtime stub for setuptools._distutils.compilers.
#
# TF's eager import chain ``tensorflow → _api/v2/compat/v1/lite →
# tensorflow.lite.python.convert → setuptools._distutils.spawn →
# setuptools._distutils.errors`` ends in, on setuptools >= 75, a
# ``from .compilers.C.errors import CompileError, ...`` statement.
# PyInstaller fails to bundle that subpackage's ``__init__.py`` files
# (collect_submodules/collect_all both miss it), leaving the workers
# to crash with ``KeyError: 'setuptools._distutils.compilers'`` inside
# ``_NamespacePath._get_parent_path``.
#
# AllWell never invokes tflite conversion, so we plant an inert stub
# tree in ``sys.modules`` before any setuptools-distutils import runs.
#
# CRITICAL: this must run BEFORE _dispatch_multiprocessing_child(). In a
# spawn-mode worker child the dispatcher calls multiprocessing.spawn_main
# synchronously and never returns (the worker process exits from inside
# that call). Any stub installed *after* the dispatch never runs in the
# very worker that needs it.
# ---------------------------------------------------------------------------

def _install_setuptools_distutils_compilers_stub() -> None:
    import types
    if "setuptools._distutils.compilers.C.errors" in sys.modules:
        return

    errors_mod = types.ModuleType("setuptools._distutils.compilers.C.errors")
    for _name in (
        "CompileError",
        "LibError",
        "LinkError",
        "PreprocessError",
        "UnknownFileError",
    ):
        errors_mod.__dict__[_name] = type(_name, (Exception,), {})

    c_mod = types.ModuleType("setuptools._distutils.compilers.C")
    c_mod.__path__ = []  # mark as package so submodule lookups don't crash
    c_mod.errors = errors_mod

    compilers_mod = types.ModuleType("setuptools._distutils.compilers")
    compilers_mod.__path__ = []
    compilers_mod.C = c_mod

    sys.modules["setuptools._distutils.compilers"] = compilers_mod
    sys.modules["setuptools._distutils.compilers.C"] = c_mod
    sys.modules["setuptools._distutils.compilers.C.errors"] = errors_mod


_install_setuptools_distutils_compilers_stub()

import multiprocessing
multiprocessing.freeze_support()

# ---------------------------------------------------------------------------
# Multiprocessing child-process dispatcher.
#
# On macOS the default start method is "spawn", which re-execs this bundled
# binary for every worker. PyInstaller's multiprocessing runtime hook
# handles ``--multiprocessing-fork`` for the pool workers, but the
# resource_tracker child invocation is hard-coded inside CPython
# (multiprocessing/resource_tracker.py::ensure_running) and bypasses that
# patch — it re-execs as ``<exe> -B -S -I -c "from
# multiprocessing.resource_tracker import main;main(N)"``. Without this
# guard those flags fall through to all_well's argparse and the worker
# dies with "unrecognized arguments: -B -S -I -c ...".
# ---------------------------------------------------------------------------

def _dispatch_multiprocessing_child() -> None:
    argv = sys.argv
    if len(argv) >= 2 and argv[1] == "--multiprocessing-fork":
        from multiprocessing.spawn import spawn_main
        kwds = {}
        for arg in argv[2:]:
            name, value = arg.split("=", 1)
            kwds[name] = int(value)
        spawn_main(**kwds)
        sys.exit()
    # multiprocessing's resource_tracker is re-exec'd as ``<bundle> -c
    # 'from multiprocessing.resource_tracker import main;main(<fd>)'`` —
    # gate on ``argv[1] == "-c"`` (not "anywhere in argv") so a user
    # path that happens to contain ``-c`` doesn't redirect into this
    # branch. The startswith-check on argv[2] is the existing safety
    # net; we keep it.
    if len(argv) >= 3 and argv[1] == "-c" and argv[2].startswith(
        "from multiprocessing.resource_tracker import main"
    ):
        exec(argv[2], {"__name__": "__main__"})
        sys.exit()


_dispatch_multiprocessing_child()

import matplotlib
matplotlib.use("QtAgg")

from pathlib import Path

if hasattr(sys, "_MEIPASS"):
    _BUNDLE_DIR = Path(sys._MEIPASS)
else:
    _BUNDLE_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(_BUNDLE_DIR))


def _apply_baked_theme() -> None:
    """Apply a build-time-baked default UI theme, if the bundle carries one.

    ``ALLWELL_THEME=<name> scripts/build_executable.sh`` writes
    ``allwell_theme.txt`` at the bundle root (mirroring the profile marker). Read
    it and ``setdefault`` the env var so ``allwell.main`` picks it up — while a
    runtime ``ALLWELL_THEME`` env var or ``--theme`` flag still overrides (soft).
    No-op when running from source or from an unthemed build.
    """
    import os
    try:
        name = (_BUNDLE_DIR / "allwell_theme.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return
    if name:
        os.environ.setdefault("ALLWELL_THEME", name)


def _dispatch() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--run-pipeline":
        # Strip the sentinel so process_microscopy's argparse sees
        # only its own flags. argv[0] stays as the executable path.
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        import process_microscopy
        process_microscopy.main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--run-zipper":
        # Same frozen-dispatch trick as ``--run-pipeline``: the Analyze tab's
        # grouping phase spawns the zipper as a subprocess, but in a frozen
        # bundle ``sys.executable`` is the .app binary, not a Python interpreter,
        # so ``[sys.executable, "pipeline/well_plate_zipper.py", ...]`` would just
        # relaunch the GUI and the zipper would never run. Route argv past the
        # sentinel into its ``main()`` (which parse_args() the rest).
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from pipeline import well_plate_zipper
        well_plate_zipper.main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--run-bootstrap":
        # Same frozen-dispatch trick again: the Analyze → Remotes dialog spawns
        # ``services/remote_bootstrap.py`` as a subprocess to provision a remote
        # host, but in a frozen bundle ``sys.executable`` is the .app binary,
        # not a Python interpreter, so ``[sys.executable, "services/remote_bootstrap.py",
        # ...]`` would just relaunch the GUI. Load the bundled module by file path
        # (it lives at ``_MEIPASS/services/remote_bootstrap.py`` so its
        # ``REPO_ROOT = __file__.parent.parent`` resolves to _MEIPASS, where the
        # required-files manifest is bundled) and route argv past the sentinel
        # into its ``main()`` (which parse_args() the rest: alias [--force]).
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        # A frozen windowed (console=False) build leaves sys.stdout / sys.stderr
        # set to None even though the parent handed us a live pipe on fd 1/2.
        # The bootstrap writes to sys.stdout directly (say(), the tee), so
        # without this repair its very first message raises AttributeError on
        # None and the Remotes dialog's log stays empty for a subprocess that
        # died before it did anything. process_microscopy and well_plate_zipper
        # call the same helper from their own main(); this entry point reaches
        # its module through importlib, so the repair belongs here.
        from util.frozen_io import ensure_std_streams
        ensure_std_streams()
        import importlib.util
        _bpath = _BUNDLE_DIR / "services" / "remote_bootstrap.py"
        _spec = importlib.util.spec_from_file_location("remote_bootstrap", _bpath)
        _mod = importlib.util.module_from_spec(_spec)
        # Register before exec_module: the module defines @dataclass classes,
        # and dataclasses resolves annotations via sys.modules[cls.__module__]
        # — absent that entry it raises AttributeError on 'NoneType'.
        sys.modules["remote_bootstrap"] = _mod
        _spec.loader.exec_module(_mod)
        sys.exit(_mod.main())
    if len(sys.argv) > 1 and sys.argv[1] == "--run-report":
        # The headless report (``python allwell_report.py …`` from source). In
        # a frozen bundle that script is not runnable on its own, so it gets
        # the same sentinel dispatch as the three above; the rest of argv is
        # the report's own flags.
        from util.frozen_io import ensure_std_streams
        ensure_std_streams()
        from allwell_report import main as report_main
        sys.exit(report_main(sys.argv[2:]))
    _apply_baked_theme()
    from allwell import main
    main()


if __name__ == "__main__":
    _dispatch()
