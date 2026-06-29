"""``--smoke`` self-test: build every Review tab and resolve every lazy proxy.

A fast, headless-capable confidence check for the feature-registry architecture
(v8.0+). It catches the two refactor-specific failure classes that unit tests
under-cover for the real GUI:

  * a tab that no longer *builds* (construction/dispatch regression), and
  * a mis-wired lazy import proxy (``core/lazy.py``) — a typo'd target name or a
    moved symbol only surfaces on first click otherwise.

It builds the app, force-builds every top-level tab + the Plotting and Image
Browser sub-tabs, then resolves every registered lazy proxy and confirms each is
an importable callable. Prints ``[PASS]`` / ``[FAIL]`` per check and returns a
process exit code (0 = all passed). Runs under the offscreen Qt platform, so it
works in CI and over SSH with no display.

Invoke via ``python allwell.py --smoke`` (optionally ``--data_dir <dir>`` to load
a dataset first and exercise the data paths too).
"""

from __future__ import annotations

import os
import traceback
from pathlib import Path


def _force_offscreen() -> None:
    # Only when no display is configured — respect an explicit platform choice.
    if not os.environ.get("QT_QPA_PLATFORM") and not os.environ.get("DISPLAY") \
            and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"


def run_smoke(data_dir: "str | Path | None" = None) -> int:
    """Run the smoke checks; return 0 if all pass, else 1."""
    _force_offscreen()
    from PySide6.QtWidgets import QApplication

    qapp = QApplication.instance() or QApplication([])
    checks: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    def step(name: str, fn) -> bool:
        try:
            fn()
            record(name, True)
            return True
        except Exception:
            record(name, False, traceback.format_exc(limit=3).strip().splitlines()[-1])
            return False

    # 1. Construct the Review app (registry build + state attach + UI).
    holder: dict = {}

    def _construct():
        from well_viewer.runtime_app import WellViewerApp
        holder["app"] = WellViewerApp()

    if not step("construct WellViewerApp", _construct):
        return _report(checks)
    app = holder["app"]

    # Optional: load a dataset so data-dependent build paths run for real.
    if data_dir:
        step(f"load dataset: {data_dir}", lambda: app._load_path(Path(data_dir)))

    # 2. Build every top-level tab (drains lazy_only too via the tab handler).
    nb = getattr(app, "_notebook", None)
    if nb is not None:
        for name in list(nb.pageNames()):
            step(f"build tab: {name}", (lambda n: lambda: (nb.setCurrentByName(n), qapp.processEvents()))(name))

    # 2b. Plotting sub-tabs (Line / Bar / Scatter / Distribution / Heat Map).
    pnb = getattr(app, "_plotting_notebook", None)
    if pnb is not None and hasattr(pnb, "pageNames"):
        for name in list(pnb.pageNames()):
            step(f"build plot sub-tab: {name}", (lambda n: lambda: (pnb.setCurrentByName(n), qapp.processEvents()))(name))

    # 2c. Image Browser sub-tabs (Segmentation / Image Table / Cell Browser / Playground).
    sub = getattr(app, "_image_browser_subtabs", None)
    if sub is not None and hasattr(sub, "count"):
        for i in range(sub.count()):
            title = sub.tabText(i)
            step(f"build image sub-tab: {title}", (lambda idx: lambda: (sub.setCurrentIndex(idx), qapp.processEvents()))(i))

    # 3. Resolve every lazy proxy created by the modules imported above.
    from well_viewer.core.lazy import verify_lazy_imports
    proxies = verify_lazy_imports()
    record(f"lazy proxies registered: {len(proxies)}", bool(proxies),
           "" if proxies else "none registered — did the tabs build?")
    for target, ok, err in proxies:
        record(f"lazy proxy: {target}", ok, err or "")

    return _report(checks)


def _report(checks: "list[tuple[str, bool, str]]") -> int:
    failed = [(n, d) for n, ok, d in checks if not ok]
    for name, ok, detail in checks:
        line = f"[{'PASS' if ok else 'FAIL'}] {name}"
        if not ok and detail:
            line += f"\n        -> {detail}"
        print(line)
    print(f"\nsmoke: {len(checks) - len(failed)}/{len(checks)} passed, {len(failed)} failed")
    if failed:
        print("FAILURES:")
        for n, d in failed:
            print(f"  - {n}: {d}")
    return 1 if failed else 0
