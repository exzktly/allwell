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

import contextlib
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

    # 11) Datasets mode — every tool page.
    print("[datasets mode]")
    _capture_datasets(app, w, rev)

    # 12) Schema detection, on both pages that offer it. Rendered with a real
    # result on screen rather than in its resting state: the readout is the
    # whole of this feature, and an empty-state grab of the control shows
    # nothing that a grab taken before it existed would not.
    print("[schema detection]")
    _capture_schema_detection(app, w, rev)

    # 13) The Playground's threshold curve, in all three of its states.
    print("[threshold curve]")
    _capture_threshold_curve(app, w)

    # 14) Field quality, on the two surfaces that carry it: the Cell Gating
    # panel's control and sentence, and the Home plate's marker. Rendered with
    # a block on screen rather than in the resting state — the whole feature
    # IS the readout, and an empty-state grab shows nothing a grab taken
    # before it existed would not.
    print("[field quality]")
    _capture_field_quality(app, w, rev)

    # 14b) The per-cell focus layer, on the three surfaces it reaches: the
    # third gate on the same Cell Gating panel, the Home plate's composite
    # marker, and the Playground's distribution window. Rendered with values
    # on screen for the same reason as the block above -- the feature IS the
    # readout.
    print("[cell focus]")
    _capture_cell_focus(app, w, rev)
    _capture_focus_distribution(app, w)

    # 14c) The Analyze form's two new Quality Control controls, in both of
    # their states: the measurement on with its threshold row showing, and the
    # measurement off with the row hidden rather than greyed out.
    print("[analyze focus controls]")
    _capture_analyze_focus(app, w, rev)

    # 15) Help drawer dialog.
    print("[dialogs]")
    try:
        w._toggle_help_drawer()
        settle(app, 300)
        grab(w, "95-dialog-help", "Help drawer overlay")
    except Exception as exc:  # pragma: no cover - best-effort dialog capture
        print(f"  help drawer skipped: {exc}")

    w.close()


def _capture_field_quality(app, w, rev) -> None:
    """Grab the Cell Gating panel and the Home plate with flags present.

    The block is injected into ``dataset.pipeline_info`` rather than produced
    by a run, for the same reason the threshold curve's counts are synthetic:
    this session installs no TensorFlow, and it is the **drawing** a reviewer
    has to see. The numbers are the shape a real plate produces — six wells,
    three fields each, one field badly out of focus.
    """
    from pipeline.field_quality import FIELD_QUALITY_BLOCK

    wells = ("A01", "A02", "A03", "B01", "B02", "B03")
    block_wells = {}
    for idx, well in enumerate(wells):
        fields = []
        for fov in range(3):
            focus = 27.0 + idx * 0.05 + fov * 0.01
            if well == "A02" and fov == 1:
                focus = 2.0
            fields.append({"fov": str(fov + 1), "timepoint": "0",
                           "focus": focus, "sat_frac": 0.0,
                           "dyn_range": 900.0 + fov, "nuc_count": 300 + fov,
                           "nuc_area_median": 150.0 + fov,
                           "bg_level": 100.0 + fov})
        block_wells[well] = {"well": well, "fields": fields,
                             "measured": 3, "measured_of": 3}
    block = {"wells": block_wells, "partial_wells": [],
             "measured": 18, "measured_of": 18}

    # The refusal states, which the first pass never rendered — and the two
    # the whole design rests on. A finding wearing the wrong register and a
    # checkbox live on a plate that refuses to be scored were both defects
    # living in states nobody had looked at.
    import copy
    partial = copy.deepcopy(block)
    partial["partial_wells"] = ["A01"]
    partial["wells"]["A01"]["measured_of"] = 5
    partial["measured_of"] = 20
    none_at_all = None

    info = rev._app_state.dataset.pipeline_info
    prior = info.get(FIELD_QUALITY_BLOCK)
    info[FIELD_QUALITY_BLOCK] = block
    try:
        # Cell Gating is a sub-tab of Home, not a rail page of its own, so
        # ``goto`` cannot reach it — it returned False and this grab was
        # silently skipped on the first run, which is the failure this whole
        # section is about wearing a harness costume. Say so rather than
        # producing a shorter manifest nobody counts.
        if not goto(app, rev, "Home"):
            print("  field quality skipped: Home page not reachable")
            return
        tab = _inner_tab(rev, "Cell Gating")
        if tab is None:
            print("  field quality gate skipped: no Cell Gating sub-tab")
        else:
            rev._drain_pending_centre_builders()
            # The panel was already built by the Home sub-tab sweep above,
            # before this block was injected, so selecting the tab is not
            # enough — the sentence is a fact about the dataset and only the
            # refresh re-reads it.
            # ``rev``, not ``w``. The widget registry stamps its handles on
            # the ReviewApp; ``w`` is the top-level window, has no
            # ``_qc_gate_widgets``, and ``qc_gate_refresh_widgets`` returns
            # silently for a host that has none — so passing it produced a
            # byte-identical screenshot and a refresh that never ran. Not
            # suppressed either: a refusal to refresh is the one thing this
            # grab exists to show, and swallowing the reason is how the first
            # version of it went unnoticed.
            from review.features.qc.tab_view import qc_gate_refresh_widgets
            qc_gate_refresh_widgets(rev)
            settle(app, 350)
            grab(w, "72-field-quality-gate",
                 "Cell Gating — the run's own field-quality verdicts, "
                 "reported and not yet applied")
        # The other two registers, on the same panel.
        for slug, payload, desc in (
                ("declined", partial,
                 "refused — the plate was only partly measured, so a "
                 "plate-wide comparison would be drawn from a biased sample"),
                ("none", none_at_all,
                 "a dataset analysed before field quality was recorded — "
                 "muted, because it is not making a statement about itself")):
            if payload is None:
                info.pop(FIELD_QUALITY_BLOCK, None)
            else:
                info[FIELD_QUALITY_BLOCK] = payload
            if _inner_tab(rev, "Cell Gating") is not None:
                qc_gate_refresh_widgets(rev)
                settle(app, 300)
                grab(w, f"72-field-quality-gate-{slug}",
                     f"Cell Gating — {desc}")
        info[FIELD_QUALITY_BLOCK] = block

        if _inner_tab(rev, "Overview") is not None:
            from review.features.home.controller import refresh_home
            with contextlib.suppress(Exception):
                refresh_home(rev._ctx)
            settle(app, 350)
            grab(w, "73-field-quality-plate",
                 "Home plate — a flagged well marked on the glyph channel, "
                 "leaving the closed colour vocabulary alone")
    finally:
        if prior is None:
            info.pop(FIELD_QUALITY_BLOCK, None)
        else:
            info[FIELD_QUALITY_BLOCK] = prior


def _capture_cell_focus(app, w, rev) -> None:
    """The per-cell focus gate and the plate composite, with values present.

    The focus column is written per cell by a run, so it is injected into the
    row cache here rather than produced — this session installs no TensorFlow
    and, more to the point, the fixture dataset predates the column. The
    values are the shape a real frame gives: a sharp population around 0.3
    with a soft corner around 0.02, which is what a focus gradient across a
    field actually looks like.
    """
    import numpy as _np

    from pipeline.field_quality_record import FOCUS_COLUMN

    cache = rev._app_state.dataset.cache
    if not cache:
        print("  cell focus skipped: no wells cached")
        return
    labels = sorted(cache)
    saved = {}
    rng = _np.random.default_rng(7)
    for idx, label in enumerate(labels):
        df = cache[label]
        if df is None or len(df) == 0:
            continue
        saved[label] = df[FOCUS_COLUMN].copy() if FOCUS_COLUMN in df.columns else None
        n = len(df)
        # One well carries a gradient (a third of it soft), the rest are
        # sharp. A plate where every well is marked shows nothing about the
        # composite; a plate where none is shows nothing either.
        soft = int(n * 0.35) if idx == 1 else int(n * 0.02)
        vals = _np.concatenate([
            rng.normal(0.30, 0.05, max(0, n - soft)).clip(0.12, None),
            rng.normal(0.03, 0.01, soft).clip(0.005, None)])
        rng.shuffle(vals)
        df[FOCUS_COLUMN] = vals[:n]

    params = rev._app_state.qc.params
    try:
        if not goto(app, rev, "Home"):
            print("  cell focus skipped: Home page not reachable")
            return
        from review.domain.qc_params import QCParams
        from review.features.qc.tab_view import qc_gate_refresh_widgets

        for slug, new_params, desc in (
                ("reported", QCParams(focus_min=0.10),
                 "the per-cell focus cut, reported and not yet applied"),
                ("applied",
                 QCParams(exclude_soft_cells=True, focus_min=0.10),
                 "the same cut switched on — the effect line is the only "
                 "thing on the panel that moves"),
                ("no-cut", QCParams(),
                 "measured with no threshold chosen — the panel points at "
                 "where a number comes from rather than inventing one")):
            rev._app_state.qc.params = new_params
            if _inner_tab(rev, "Cell Gating") is not None:
                qc_gate_refresh_widgets(rev)
                settle(app, 300)
                grab(w, f"74-cell-focus-gate-{slug}", f"Cell Gating — {desc}")

        rev._app_state.qc.params = QCParams(focus_min=0.10)
        if _inner_tab(rev, "Overview") is not None:
            from review.features.home.controller import refresh_home
            with contextlib.suppress(Exception):
                refresh_home(rev._ctx)
            settle(app, 350)
            grab(w, "75-cell-focus-plate",
                 "Home plate — a well marked by the composite for a focus "
                 "gradient its fields' own verdicts cannot see")
    finally:
        rev._app_state.qc.params = params
        for label, col in saved.items():
            df = cache.get(label)
            if df is None:
                continue
            if col is None:
                df.drop(columns=[FOCUS_COLUMN], inplace=True, errors="ignore")
            else:
                df[FOCUS_COLUMN] = col


def _capture_focus_distribution(app, w) -> None:
    """The Playground's focus-distribution window, in three states.

    Drawn from values rather than from a live segmentation, for the same
    reason the threshold curve beside it is: the drawing is what a reviewer
    has to see. The three are the ones a user actually meets — a frame with a
    soft population and a cut placed in the gap, the same frame with no cut
    chosen, and a frame where nothing could be measured at all.
    """
    import numpy as _np

    from PySide6.QtWidgets import QDialog, QVBoxLayout
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    from review.domain.focus_distribution import distribution_from_values
    from review.features.image_browser.playground.focus_curve_view import (
        draw_focus_distribution,
    )

    rng = _np.random.default_rng(11)
    sharp = rng.normal(0.30, 0.055, 260).clip(0.12, None)
    soft = rng.normal(0.035, 0.012, 95).clip(0.006, None)
    values = [float(v) for v in _np.concatenate([sharp, soft])]
    values += [None] * 12

    states = (
        ("cut", distribution_from_values(
            values, cut=0.10, subject="DAPI · B03 · FOV 2 · live StarDist"),
         "a frame with a soft population, the user's own cut marked and its "
         "consequence on the figure"),
        ("no-cut", distribution_from_values(
            values, subject="DAPI · B03 · FOV 2 · live StarDist"),
         "the same frame with no cut chosen — nothing is suggested, and the "
         "figure says that was measured rather than cautious"),
        ("nothing", distribution_from_values(
            [None] * 40, subject="DAPI · B03 · FOV 2 · live StarDist"),
         "a frame where no outline could be measured — the refusal is the "
         "figure, and it is itself a finding about the frame"),
    )
    for slug, dist, desc in states:
        popup = QDialog(w)
        popup.setWindowTitle("Focus distribution")
        popup.resize(820, 460)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        fig = Figure(figsize=(7.8, 4.4), dpi=100)
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)
        draw_focus_distribution(fig, dist)
        canvas.draw()
        popup.show()
        settle(app, 250)
        grab(popup, f"76-focus-distribution-{slug}",
             f"Focus distribution — {desc}")
        popup.close()


def _capture_analyze_focus(app, w, rev) -> None:
    """The Analyze form's Quality Control section, with the focus controls.

    Both states, because the threshold row **hides** with the measurement
    rather than greying out, and a greyed-out orphan field is exactly what
    that choice exists to avoid — so the off state is as much of the design as
    the on one.
    """
    analyze = getattr(rev, "_analyze", None) or getattr(w, "_analyze", None)
    if analyze is None or not hasattr(analyze, "_focus_metric_cb"):
        print("  analyze focus controls skipped: form not built")
        return
    # Index 1 is the Analyze pane, as the mode grab above establishes.
    rev._central_pane_stack.setCurrentIndex(1)
    settle(app, 400)
    was_on = analyze._focus_metric_cb.isChecked()
    was_cut = analyze._focus_min_edit.text()
    try:
        analyze._focus_metric_cb.setChecked(True)
        analyze._focus_min_edit.setText("0.083")
        settle(app, 300)
        grab(w, "93-analyze-focus-on",
             "Analyze ▸ Quality Control — per-cell focus measured by default, "
             "with a cut recorded rather than applied")
        analyze._focus_metric_cb.setChecked(False)
        settle(app, 300)
        grab(w, "93-analyze-focus-off",
             "Analyze ▸ Quality Control — the measurement off, and the "
             "threshold row hidden rather than left as a greyed-out orphan")
    finally:
        analyze._focus_metric_cb.setChecked(was_on)
        analyze._focus_min_edit.setText(was_cut)
        rev._central_pane_stack.setCurrentIndex(0)
        settle(app)


def _inner_tab(rev, title: str):
    """Select the inner ``QTabWidget`` tab named *title*; ``None`` if absent."""
    for tw in rev.findChildren(QTabWidget):
        if not tw.isVisible():
            continue
        for i in range(tw.count()):
            if tw.tabText(i).strip() == title:
                tw.setCurrentIndex(i)
                return tw
    return None


def _capture_threshold_curve(app, w) -> None:
    """Grab the threshold-curve window: a real sweep, a refusal, and a stop.

    Drawn from synthetic counts rather than from a real sweep, because the
    session installs no TensorFlow (CLAUDE.md §10.6) and the drawing is the
    part a UX reviewer has to see. The counts are the shape a real 512x512
    frame produced against StarDist 0.8.5 in an isolated venv, so the curve
    has the real thing's proportions rather than an invented sag.
    """
    import math

    from PySide6.QtWidgets import QDialog, QVBoxLayout
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    from review.domain.threshold_sweep import curve_from_counts, sweep_grid
    from review.features.image_browser.playground.sweep_curve_view import (
        draw_threshold_curve,
    )

    grid = sweep_grid()
    measured = [int(round(400 / (1 + math.exp((t - 0.62) / 0.045)))) for t in grid]
    common = dict(requested=len(grid), requested_span=(grid[0], grid[-1]),
                  model_default=0.479071, subject="GFP · B03 · FOV 1 · 24h")
    cases = (
        ("ok", curve_from_counts(grid, measured, current=0.2, **common),
         "the user's threshold against the model's own default"),
        ("stopped", curve_from_counts(grid[:8], measured[:8], current=0.2,
                                      **common),
         "stopped early — the unmeasured range stays visibly empty"),
        ("declined", curve_from_counts(grid, [0] * len(grid), **common),
         "no nuclei at any threshold: the refusal is the figure"),
    )
    for slug, curve, desc in cases:
        dlg = QDialog(w)
        dlg.setWindowTitle("Threshold curve")
        dlg.resize(820, 460)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(8, 8, 8, 8)
        fig = Figure(figsize=(7.8, 4.4), dpi=100)
        canvas = FigureCanvas(fig)
        lay.addWidget(canvas)
        draw_threshold_curve(fig, curve)
        dlg.show()
        settle(app, 250)
        canvas.draw()
        grab(dlg, f"94-threshold-curve-{slug}", f"Threshold curve — {desc}")
        dlg.close()


def _build_detect_corpus(root: Path, *, parses: bool) -> Path:
    """A folder of filenames for the detection grabs.

    *parses* picks between a plate the inference reads cleanly — with one name
    it cannot, so the readout has an exception to quote — and a folder of names
    that share no layout at all, which is the refusal.
    """
    root.mkdir(parents=True, exist_ok=True)
    if not parses:
        for i in range(8):
            (root / f"scan{i}.tif").write_bytes(b"")
        return root
    for well in ("A01", "A02", "A03", "B01", "B02", "B03"):
        for fov in ("f0", "f1"):
            for chan in ("DAPI", "GFP"):
                (root / f"Exp01_{chan}_{well}_{fov}_t0.tif").write_bytes(b"")
    (root / "Exp01_GFP_ZZ9_f0_t0.tif").write_bytes(b"")
    return root


def _capture_datasets(app, w, rev) -> None:
    """Grab every Datasets tool page.

    The audit had never entered this pane at all — it switched the centre stack
    between Review (0) and Analyze (1) and stopped — so a third of the
    application's pages had no rendering to review against.
    """
    tab = getattr(w, "_datasets", None)
    if tab is None:
        print("  datasets pane absent (profiled out?)")
        return
    rev._central_pane_stack.setCurrentIndex(2)
    settle(app, 400)
    for i, name in enumerate(tab._pages.pageNames()):
        tab._show_page(name)
        settle(app, 250)
        grab(w, f"91-datasets-{i}-{_slug(name)}", f"Datasets → {name}")
    rev._central_pane_stack.setCurrentIndex(0)
    settle(app)


def _capture_schema_detection(app, w, rev) -> None:
    """Grab the Filename Schema control on both hosts, holding a real result."""
    root = Path(tempfile.mkdtemp()) / "detect"
    good = _build_detect_corpus(root / "plate", parses=True)
    bad = _build_detect_corpus(root / "junk", parses=False)

    analyze = getattr(w, "_analyze", None)
    if analyze is not None and getattr(analyze, "_schema_picker", None) is not None:
        rev._central_pane_stack.setCurrentIndex(1)
        settle(app, 300)
        for slug, folder, desc in (
            ("ok", good, "a layout it reads, with one name it cannot"),
            ("refused", bad, "a folder with no detectable layout"),
        ):
            analyze._input_edit.setText(str(folder))
            analyze._schema_picker.detectButton().click()
            settle(app, 250)
            grab(w, f"92-analyze-detect-{slug}",
                 f"Analyze → Filename Schema, detection: {desc}")
        rev._central_pane_stack.setCurrentIndex(0)
        settle(app)

    tab = getattr(w, "_datasets", None)
    if tab is None:
        return
    rev._central_pane_stack.setCurrentIndex(2)
    tab._show_page("Zip Loose Folder")
    settle(app, 300)
    page = tab._pages.currentWidget()
    picker = getattr(page, "_schema_picker", None)
    if picker is not None and picker.detectButton() is not None:
        page._src_field.set_path(str(good))
        picker.detectButton().click()
        settle(app, 250)
        grab(w, "93-loose-detect-ok",
             "Zip Loose Folder → Filename Schema, detection applied")
    rev._central_pane_stack.setCurrentIndex(0)
    settle(app)


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
