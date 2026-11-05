"""One (dataset, screen) case of the page-render test, run in a process of its own.

A full app's widgets are never reclaimed in-process (CLAUDE.md §2), and every later restyle walks all of
them, so nine apps in one process slowed from seconds per page to minutes per page. Each case therefore
runs here, in a fresh interpreter, and writes what it found to a JSON file for
``tests/test_every_page_renders.py`` to assert on::

    python -m tests._page_render_case <dataset> <screen> <out.json>

The environment the test suite sets (offscreen platform, a private state directory, desktop layout at
startup so the phone is entered by the button a person presses) is set by the caller.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


def run_case(kind: str, screen: str, workdir: Path) -> list[dict]:
    from PySide6.QtWidgets import QApplication

    from tests._layout_checks import check_window
    from tests._page_datasets import build
    from tests._page_tour import tour
    from tests._screens import SCREENS, FakeScreen, is_phone, place, pump
    from widgets.empty_state import EmptyState

    qapp = QApplication.instance() or QApplication([])
    from allwell import AllWellApp

    rect, phone = SCREENS[screen], is_phone(screen)
    shots = os.environ.get("ALLWELL_PAGE_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
    pages: list[dict] = []
    with FakeScreen(rect):
        data, wells = build(kind, workdir)
        win = AllWellApp(None)
        win.show()
        pump(qapp)
        win._review._load_path(data)
        pump(qapp, 60, 400)
        win._review._set_selected_wells(set(wells), commit=True)
        pump(qapp, 40, 400)
        shell = win._phone
        if phone:
            shell.desk_button.click()
            pump(qapp, 60, 300)
        place(win, rect, qapp)
        for page in tour(win, qapp, kind=kind, wells=wells, phone=phone):
            pump(qapp, 20, 450)
            if phone:
                shell._sweep(force=False)
                pump(qapp, 10, 100)
            findings = check_window(win, shell if phone else None)
            pages.append({
                "page": page,
                "findings": [list(f) for f in findings],
                "empty": any(w.isVisibleTo(win) for w in win.findChildren(EmptyState)),
            })
            if shots:
                win.grab().save(str(Path(shots) / f"{kind}_{screen}_{_slug(page)}.png"))
    return pages


def main(argv: list[str]) -> int:
    kind, screen, out = argv
    workdir = Path(out).parent / f"{kind}-{screen}-data"
    workdir.mkdir(parents=True, exist_ok=True)
    pages = run_case(kind, screen, workdir)
    Path(out).write_text(json.dumps(pages), encoding="utf-8")
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    # A full app does not tear down cleanly in-process; the result is on disk.
    os._exit(code)
