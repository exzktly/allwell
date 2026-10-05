"""Before/after capture of the Properties panel, for refactors meant to change nothing.

Run::

    QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg \\
        python tests/visual/render_properties_panel.py <out_dir>

``render_views.py`` walks the app's views but never opens the Properties dock,
so it cannot show a change to the panel. This builds the real
``ExportStyleSidebar`` for each figure kind whose panel differs (Line, Bar, Heat
Map, per-cell Scatter, Response Curves, and a figure bound to none of them),
against the same stub app the Properties tests use, and writes per kind:

* ``<kind>.png`` and ``<kind>_expanded.png`` — the panel as opened, and with
  every section expanded;
* one ``fingerprint.json`` — the widget tree (class, object name, text, value,
  tooltip, enabled, hidden, grid positions), the panel's instance attributes,
  its bound pref keys, the sections and whether each opens expanded, and how
  many SEM / FOV observers it registered.

Run it on both trees into two directories, then compare: the JSON with
``cmp``, the PNGs pixel by pixel. Both are deterministic on one machine — the
grabs wait out the toggle-switch animation, which otherwise leaves a few
hundred pixels different between two runs of the same code — so any
difference is the change. Keep the output outside the repository.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

KINDS = ("_line_fig", "_bar_fig", "_heatmap_fig", "_scatter_fig",
         "_response_fig", "_other_fig")
#: Long enough for a ToggleSwitch's knob animation to finish before a grab.
SETTLE_MS = 600


def _describe(w: QWidget) -> dict:
    d: dict = {"cls": type(w).__name__, "name": w.objectName()}
    if isinstance(w, QLabel):
        d["text"] = w.text()
    if isinstance(w, QAbstractButton):
        d["text"] = w.text()
        d["checked"] = w.isChecked()
    if isinstance(w, QComboBox):
        d["items"] = [w.itemText(i) for i in range(w.count())]
        d["cur"] = w.currentText()
    if isinstance(w, QLineEdit):
        d["text"] = w.text()
        d["ph"] = w.placeholderText()
    if isinstance(w, QAbstractSpinBox):
        d["text"] = w.text()
    if type(w).__name__ == "Stepper":
        d["value"] = w.value()
    d["tip"] = w.toolTip()
    d["enabled"] = w.isEnabled()
    d["hidden"] = w.isHidden()
    layout = w.layout()
    if isinstance(layout, QGridLayout):
        cells = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            r, c, rs, cs = layout.getItemPosition(i)
            child = item.widget()
            cells.append([r, c, rs, cs,
                          type(child).__name__ if child else type(item).__name__])
        d["grid"] = cells
    d["kids"] = [_describe(c) for c in w.children() if isinstance(c, QWidget)]
    return d


def capture(out_dir: Path) -> dict:
    from matplotlib.figure import Figure

    from review.features.export.sidebar_view import ExportStyleSidebar
    from tests.test_properties_hides_what_it_cannot_do import _stub_app
    from ui import theme
    from widgets.collapsible_section import CollapsibleSection

    qapp = QApplication.instance() or QApplication([])
    qapp.setStyleSheet(theme.qss())
    # Nothing to redraw against a stub app; the panel only needs to be built.
    ExportStyleSidebar._redraw_bound_figure = lambda self: True
    ExportStyleSidebar._redraw_active_plot = lambda self: None

    result = {}
    for kind in KINDS:
        fig = Figure(figsize=(4, 3))
        fig.add_subplot(1, 2, 1)
        second = fig.add_subplot(1, 2, 2)
        if kind == "_heatmap_fig":
            second._aw_colorbar = True
        app = _stub_app()
        setattr(app, kind, fig)
        sb = ExportStyleSidebar(app, None, fig, None, default_name=kind)
        sb.resize(sb.width(), 2400)
        sb.show()
        QTest.qWait(SETTLE_MS)
        sections = sb.findChildren(CollapsibleSection)
        result[kind] = {
            "attrs": sorted(vars(sb)),
            "getters": sorted(sb._getters),
            "setters": sorted(sb._setters),
            "sections": [(s.titleText(), s.isExpanded()) for s in sections],
            "sem_obs": len(app._ctx.runtime.sem.observers or []),
            "fov_obs": len(app._ctx.runtime.fov.observers or []),
            "tree": _describe(sb),
        }
        sb.grab().save(str(out_dir / f"{kind}.png"))
        for s in sections:
            s.setExpanded(True)
        QTest.qWait(SETTLE_MS)
        sb.grab().save(str(out_dir / f"{kind}_expanded.png"))
        sb.deleteLater()
        qapp.processEvents()
    (out_dir / "fingerprint.json").write_text(
        json.dumps(result, indent=1, sort_keys=True, default=str))
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    os.chdir(REPO_ROOT)
    done = capture(out)
    print({kind: len(rec["attrs"]) for kind, rec in done.items()})
