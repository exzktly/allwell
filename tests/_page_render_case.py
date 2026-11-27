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
import tempfile
import traceback
from pathlib import Path


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


def start_window(qapp):
    """Build the window as ``allwell.main`` does: the bundled fonts, then the shipped theme, then the window.

    The fonts, so the pages are measured in the face the app ships rather than in whatever this machine's
    fontconfig makes of "Sans Serif" — DejaVu Sans on the CI runner. The theme, because the token classes in
    ``ui/theme.py`` hold the *blue* baseline until a theme is applied, and only ``allwell.main`` applies one:
    a window built without this call is painted in a palette no user starts in, and every screenshot taken
    from it shows that palette. ``apply_startup_palette`` is the function ``allwell.main`` calls: it honours
    ``ALLWELL_THEME``, so a run can render either palette, and then the palette stored in the run's own
    settings file; with neither, it applies ``DEFAULT_THEME``, which is what a new user sees.
    ``tests/test_the_render_harness_shows_the_shipped_theme.py`` holds both halves.
    """
    from allwell import AllWellApp, use_bundled_fonts
    from ui.palette_setting import apply_startup_palette

    use_bundled_fonts(qapp)
    apply_startup_palette()
    return AllWellApp(None)


def run_case(kind: str, screen: str, workdir: Path) -> list[dict]:
    from PySide6.QtWidgets import QApplication

    from tests._layout_checks import check_window
    from tests._page_datasets import build
    from tests._page_tour import tour
    from tests._screens import SCREENS, FakeScreen, is_phone, place, pump
    from widgets.empty_state import EmptyState

    qapp = QApplication.instance() or QApplication([])

    rect, phone = SCREENS[screen], is_phone(screen)
    shots = os.environ.get("ALLWELL_PAGE_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
    pages: list[dict] = []
    with FakeScreen(rect):
        data, wells = build(kind, workdir)
        win = start_window(qapp)
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


#: Seconds into a case at which every thread's stack is written to stderr, once. The test that runs the cases
#: gives each 1,500 s and then kills it; a case killed there left nothing to say where it had stopped, which is how
#: one that ran to its limit on the slow lane's runner could not be told from a slow one. Well inside that limit,
#: and well past the three minutes a case takes.
STUCK_AFTER_S = 1200


def main(argv: list[str]) -> int:
    import faulthandler

    faulthandler.dump_traceback_later(STUCK_AFTER_S, exit=False, file=sys.stderr)
    kind, screen, out = argv
    # A directory of its own for every run: the datasets are written with mkdir(parents=True), so one left over
    # from an earlier run (next to a reused output path) made the next run fail.
    workdir = Path(tempfile.mkdtemp(prefix=f"pages-{kind}-{screen}-"))
    pages = run_case(kind, screen, workdir)
    Path(out).write_text(json.dumps(pages), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except BaseException:
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    # A full app does not tear down cleanly in-process: a failure left to unwind hangs in interpreter
    # shutdown with the widgets still alive, and whoever waits for the process waits for ever. Say what
    # happened, then leave at once — on success and on failure alike.
    os._exit(code)
