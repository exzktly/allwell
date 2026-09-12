"""Headless renderer for every AllWell view → PNG (visual UI audit).

Run::

    QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg python tests/visual/render_views.py

Builds the deterministic ``mini_dataset`` (CSV-only), constructs the real
``AllWellApp`` off-screen, walks every rail section / notebook page / inner
sub-tab / mode / dialog, and writes a PNG per view to
``_Docs/visual_audit/screenshots/``. Re-runnable for before/after comparisons.

Image-bearing views (Image Table, Segmentation, smFISH, Playground) need
per-well image archives the CSV fixture does not produce, so they render their
empty / placeholder state — that is intentional audit coverage of empty states.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Headless Qt + non-interactive matplotlib must be set before any Qt/mpl import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFontDatabase, QPainter
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget
from PySide6.QtTest import QTest
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

OUT_DIR = REPO_ROOT / "_Docs" / "visual_audit" / "screenshots"
WIN_W, WIN_H = 1640, 980

_manifest: list[tuple[str, str]] = []


# ── infra ────────────────────────────────────────────────────────────────
def setup_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    from ui import theme as th
    from startup.app_fonts import register_qt_fonts

    # Mirror allwell.main(): prefer the bundled Inter / JetBrains Mono so the
    # rendered screenshots match what ships, falling back to platform faces.
    ui_family, mono_family = register_qt_fonts()
    th.Typography.family = ui_family or app.font().family()
    if ui_family:
        f = app.font(); f.setFamily(ui_family); app.setFont(f)
    th.Typography.family_mono = mono_family or QFontDatabase.systemFont(
        QFontDatabase.SystemFont.FixedFont
    ).family()
    # Honour ALLWELL_THEME so the audit can render any registered theme (e.g.
    # ALLWELL_THEME=grey) — applied before qss() and before any view
    # is built, exactly as allwell.main() does.
    th.select_theme_from_env()
    app.setStyleSheet(th.qss())
    return app


def settle(app: QApplication, ms: int = 250) -> None:
    """Pump the event loop so layouts settle and matplotlib canvases paint."""
    app.processEvents()
    QTest.qWait(ms)
    app.processEvents()


def grab(widget: QWidget, slug: str, desc: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{slug}.png"
    # Force a *synchronous* draw on every matplotlib canvas first. Tabs render
    # via ``draw_idle()`` (deferred), so a bare ``widget.grab()`` can capture a
    # stale/blank Agg buffer.
    canvases = [c for c in widget.findChildren(FigureCanvasQTAgg) if c.isVisible()]
    for canvas in canvases:
        canvas.draw()
    QApplication.processEvents()
    pixmap = widget.grab()
    # Offscreen window-grab can fail to blit deeply-nested canvases (e.g. the
    # heatmap, which sits inside an extra dock + empty-state stack) — its region
    # came out white though the figure rendered dark. Grabbing each canvas
    # directly works, so composite those over the window pixmap.
    painter = QPainter(pixmap)
    for canvas in canvases:
        painter.drawPixmap(canvas.mapTo(widget, QPoint(0, 0)), canvas.grab())
    painter.end()
    pixmap.save(str(path))
    size = path.stat().st_size
    _manifest.append((slug, desc))
    print(f"  saved {slug}.png ({size:,} B) — {desc}")


# ── app construction ───────────────────────────────────────────────────────
def make_app(load: bool):
    from allwell import AllWellApp

    w = AllWellApp(data_path=None)
    w.resize(WIN_W, WIN_H)
    w.show()
    QApplication.processEvents()
    if not load:
        return w, None

    from tests.fixtures.mini_dataset import build_mini_dataset, WELLS
    from review.core.selection_controller import rebuild_all

    dataset = build_mini_dataset(Path(tempfile.mkdtemp()) / "mini", with_shape=True)
    rev = w._review
    rev._load_path(dataset)
    QApplication.processEvents()
    rev._set_selected_wells(set(WELLS), commit=False)
    # ``rebuild_all`` takes the frozen ViewerContext, not the window. Passing the
    # window resolves ``ctx.window`` to ``QWidget.window`` — a bound method — and
    # the first attribute the controller reaches for raises AttributeError.
    rebuild_all(rev._ctx)
    return w, rev


# Image-review surfaces are now sub-tabs of the unified "Image Browser" rail
# entry, not top-level pages.
_IMAGE_BROWSER_SUBTABS = ("Segmentation", "Image Table", "Cell Browser", "Playground")


def goto(app, rev, page: str) -> bool:
    """Switch the centre notebook to *page*; force lazy body build + settle.

    *page* may be a top-level page name or an Image Browser sub-tab leaf
    ("Segmentation" / "Image Table" / "Cell Browser" / "Playground"), in which
    case it navigates to the Image Browser rail entry and selects the sub-tab.
    """
    nb = rev._notebook
    if page in _IMAGE_BROWSER_SUBTABS:
        from review.features.image_browser.controller import show_image_browser_subtab
        ok = show_image_browser_subtab(rev._ctx, page)
    else:
        ok = nb.setCurrentByName(page)
    rev._drain_pending_centre_builders()
    settle(app)
    return ok


# ── capture passes ─────────────────────────────────────────────────────────
def capture_empty(app) -> None:
    print("[empty state]")
    w, _ = make_app(load=False)
    settle(app)
    grab(w, "00-empty-state", "App launched with no dataset loaded")
    w.close()


def capture_review(app) -> None:
    print("[review mode]")
    w, rev = make_app(load=True)
    settle(app, 400)

    nb = rev._notebook
    pages = list(nb.pageNames())
    print("  pages:", pages)

    # 1) Plotting + its 5 inner sub-tabs (a custom NamedPageStack, not a
    #    QTabWidget) plus the Scatter Plot sub-modes.
    if goto(app, rev, "Plotting"):
        pnb = getattr(rev, "_plotting_notebook", None)
        subs = list(pnb.pageNames()) if pnb is not None else []
        for sub in subs:
            pnb.setCurrentByName(sub)
            rev._drain_pending_centre_builders()
            settle(app, 350)
            grab(w, f"10-plotting-{_slug(sub)}", f"Plotting → {sub}")
            if sub == "Scatter Plot":
                _capture_scatter_modes(app, w, rev)

    # 2) Statistics.
    if goto(app, rev, "Statistics"):
        grab(w, "20-statistics", "Statistics — group comparison")

    # 3) Multivariate — one grab per analysis mode. The modes are a
    #    SegmentedControl, not an inner QTabWidget, so _capture_inner cannot see
    #    them; each swaps a different per-mode option group into the control row.
    if goto(app, rev, "Multivariate"):
        _capture_multivariate_modes(app, w, rev)

    # 4) Spatial.
    if goto(app, rev, "Spatial"):
        grab(w, "26-spatial", "Spatial — per-well cell positions")

    # 5) Image Browser + its four sub-tabs. "Image Table" and "Segmentation" are
    #    both leaves of this one rail entry, so a separate top-level grab for
    #    each only re-shot whichever sub-tab happened to be current.
    if goto(app, rev, "Segmentation"):
        _capture_inner(app, w, rev, _current_page(rev), "30-image-browser",
                       "Image Browser", fallback="30-image-browser")

    # 5) smFISH (lazy, image-dependent → empty state).
    if goto(app, rev, "smFISH"):
        grab(w, "50-smfish", "smFISH (no image data — empty state)")

    # 6) Review CSV.
    if goto(app, rev, "Review CSV"):
        grab(w, "60-review-csv", "Review CSV — per-cell table")

    # 7) Home + its tabs (Overview + the definition editors).
    if goto(app, rev, "Home"):
        _capture_inner(app, w, rev, _current_page(rev), "70-home",
                       "Home", fallback="70-home")

    # 8) Batch Export.
    if goto(app, rev, "Batch Export"):
        grab(w, "80-batch-export", "Batch Export — multi-well figure export")

    # 9) Saved Plots.
    if goto(app, rev, "Saved Plots"):
        grab(w, "85-saved-plots", "Saved Plots — pinned figures")

    # 10) Analyze mode.
    print("[analyze mode]")
    rev._central_pane_stack.setCurrentIndex(1)
    settle(app, 400)
    grab(w, "90-analyze", "Analyze mode — pipeline runner")
    rev._central_pane_stack.setCurrentIndex(0)
    settle(app)

    # 11) Help drawer dialog.
    print("[dialogs]")
    try:
        w._toggle_help_drawer()
        settle(app, 300)
        grab(w, "95-dialog-help", "Help drawer overlay")
    except Exception as exc:  # pragma: no cover - best-effort dialog capture
        print(f"  help drawer skipped: {exc}")

    w.close()


def _current_page(rev) -> QWidget:
    nb = rev._notebook
    name = nb.currentName()
    return rev._tab_frames.get(name) if hasattr(rev, "_tab_frames") else rev


def _capture_scatter_modes(app, w, rev) -> None:
    """Grab the Scatter Plot page once per mode.

    Its two modes are the ``View`` toggle in the shared control band driving a
    ``NamedPageStack``, not an inner ``QTabWidget``, so the tab-widget search
    finds nothing here. Degrades to one plain grab if the toggle is absent.
    """
    toggle = getattr(rev, "_plotting_scatter_view", None)
    if toggle is None:
        grab(w, "11-scatter", "Scatter Plot")
        return
    for i in range(toggle.count()):
        toggle.setCurrentIndex(i)
        rev._drain_pending_centre_builders()
        settle(app, 300)
        label = toggle.segmentText(i)
        grab(w, f"11-scatter-{_slug(label)}", f"Scatter Plot → {label}")


def _capture_multivariate_modes(app, w, rev) -> None:
    """Grab the Multivariate tab once per analysis mode.

    Its four modes are a ``SegmentedControl``, not an inner ``QTabWidget``, so
    ``_capture_inner`` finds nothing and the tab was only ever shot in whichever
    mode happened to be current. Each mode swaps a different option group into
    the control row, and the widest of them (Faceting) is the only one that
    makes the row wrap — so a single-mode grab could not show a wrapped row.

    Degrades to one plain grab when the feature is profiled out.
    """
    try:
        from review.features.multivariate import controller as mv_ctrl
        from review.features.multivariate import state as mv_state
    except ImportError:
        grab(w, "25-multivariate", "Multivariate")
        return
    modes = (
        (mv_state.MODE_CORRELATION, "Correlation"),
        (mv_state.MODE_DIMRED, "Dim. Reduction"),
        (mv_state.MODE_CLUSTERING, "Clustering"),
        (mv_state.MODE_FACET, "Faceting"),
    )
    for key, label in modes:
        mv_ctrl.mv_set_mode(rev._ctx, key)
        settle(app, 300)
        grab(w, f"25-multivariate-{_slug(label)}", f"Multivariate → {label}")


def _capture_inner(app, w, rev, page: QWidget, prefix: str, title: str,
                   fallback: str | None = None) -> None:
    """Iterate the inner QTabWidget of a multi-sub-tab page, grabbing each."""
    tw = None
    for cand in (page, rev):
        if cand is None:
            continue
        for t in cand.findChildren(QTabWidget):
            if t.isVisible() and t.count() > 1:
                tw = t
                break
        if tw is not None:
            break
    if tw is None:
        if fallback:
            grab(w, fallback, f"{title} (single view)")
        return
    for i in range(tw.count()):
        tw.setCurrentIndex(i)
        rev._drain_pending_centre_builders()
        settle(app, 300)
        sub = tw.tabText(i)
        grab(w, f"{prefix}-{_slug(sub)}", f"{title} → {sub}")


def _slug(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in text).strip("-")


def main() -> int:
    app = setup_app()
    capture_empty(app)
    capture_review(app)
    print(f"\n{len(_manifest)} screenshots written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
