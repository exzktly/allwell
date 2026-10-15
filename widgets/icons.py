"""Inline icon set + recoloring renderer for the widgets package.

A small set of Lucide-style line glyphs (24×24 viewBox, 2-unit stroke, round
caps) stored as SVG path fragments. ``make_icon`` renders one at an arbitrary
size in an arbitrary token color, device-pixel-ratio aware, with a cache.

This intentionally avoids shipping a binary icon font / SVG asset folder; when
the real Lucide set is bundled later, only ``_GLYPHS`` and ``make_pixmap`` need
to change — ``IconButton`` and callers stay the same.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ui import theme

# name -> inner SVG markup (everything between <svg> tags)
_GLYPHS: dict[str, str] = {
    # Standard Feather "copy" — two overlapping rounded rectangles.
    "copy":          ('<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>'
                      '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'),
    # Lucide "microscope" — the Datasets rail's Convert CZI entry. An icon
    # name with no glyph here falls back to "alert-triangle", which makes a
    # perfectly ordinary page look like it is reporting a fault, so a new rail
    # entry adds its glyph in the same commit as itself.
    "microscope":    ('<path d="M6 18h8"/><path d="M3 22h18"/>'
                      '<path d="M14 22a7 7 0 1 0 0-14h-1"/>'
                      '<path d="M9 14h2"/>'
                      '<path d="M9 12a2 2 0 0 1-2-2V6h6v4a2 2 0 0 1-2 2Z"/>'
                      '<path d="M12 6V3a1 1 0 0 0-1-1H9a1 1 0 0 0-1 1v3"/>'),
    # Lucide "server" — the Analyze pane's Slurm-job indicator.
    "server":        ('<rect x="2" y="2" width="20" height="8" rx="2" ry="2"/>'
                      '<rect x="2" y="14" width="20" height="8" rx="2" ry="2"/>'
                      '<line x1="6" y1="6" x2="6.01" y2="6"/>'
                      '<line x1="6" y1="18" x2="6.01" y2="18"/>'),
    "chevron-down":  '<polyline points="6 9 12 15 18 9"/>',
    "chevron-right": '<polyline points="9 18 15 12 9 6"/>',
    "chevron-left":  '<polyline points="15 18 9 12 15 6"/>',
    "x":             '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    # Lucide "trash-2" — used for the destructive "reset all saved state" action.
    "trash-2":       ('<path d="M3 6h18"/>'
                      '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
                      '<line x1="10" y1="11" x2="10" y2="17"/>'
                      '<line x1="14" y1="11" x2="14" y2="17"/>'),
    "plus":          '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "search":        '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "sliders":       ('<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>'
                      '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>'
                      '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>'
                      '<line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/>'
                      '<line x1="17" y1="16" x2="23" y2="16"/>'),
    "home":          ('<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
                      '<polyline points="9 22 9 12 15 12 15 22"/>'),
    "arrow-left":    '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
    "arrow-right":   '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
    "move":          ('<polyline points="5 9 2 12 5 15"/><polyline points="9 5 12 2 15 5"/>'
                      '<polyline points="15 19 12 22 9 19"/><polyline points="19 9 22 12 19 15"/>'
                      '<line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/>'),
    "zoom-in":       ('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'
                      '<line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>'),
    "save":          ('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>'
                      '<polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>'),
    "download":      ('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                      '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'),
    "more-horizontal": '<circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/><circle cx="5" cy="12" r="1.4"/>',
    "check":         '<polyline points="20 6 9 17 4 12"/>',
    "alert-triangle": ('<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
                       '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>'),
    "info":          ('<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/>'
                      '<line x1="12" y1="8" x2="12.01" y2="8"/>'),
    "image":         ('<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>'
                      '<circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>'),
    "grid":          ('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>'
                      '<rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>'),

    # ── Phase 9 reconciliation additions ────────────────────────────────
    # Lucide-flavoured SVG paths (lucide-icons MIT). Section-nav icons:
    "line-chart":    '<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>',
    "dna":           ('<circle cx="12" cy="12" r="9"/>'
                      '<circle cx="9" cy="9.5" r="0.5"/>'
                      '<circle cx="14.5" cy="10" r="0.5"/>'
                      '<circle cx="10" cy="14" r="0.5"/>'
                      '<circle cx="15" cy="14.5" r="0.5"/>'
                      '<circle cx="12" cy="11.5" r="0.5"/>'),
    "sigma":         '<path d="M18 7V5H6l6 7-6 7h12v-2"/>',
    "columns-2":     ('<rect x="3" y="3" width="18" height="18" rx="2"/>'
                      '<path d="M12 3v18"/>'),
    "layout-grid":   ('<rect x="3" y="3" width="7" height="7" rx="1"/>'
                      '<rect x="14" y="3" width="7" height="7" rx="1"/>'
                      '<rect x="3" y="14" width="7" height="7" rx="1"/>'
                      '<rect x="14" y="14" width="7" height="7" rx="1"/>'),
    "scan-line":     ('<path d="M3 7V5a2 2 0 0 1 2-2h2"/>'
                      '<path d="M17 3h2a2 2 0 0 1 2 2v2"/>'
                      '<path d="M21 17v2a2 2 0 0 1-2 2h-2"/>'
                      '<path d="M7 21H5a2 2 0 0 1-2-2v-2"/>'
                      '<line x1="7" y1="12" x2="17" y2="12"/>'),
    "file-spreadsheet": ('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
                         '<polyline points="14 2 14 8 20 8"/>'
                         '<line x1="8" y1="13" x2="16" y2="13"/>'
                         '<line x1="8" y1="17" x2="16" y2="17"/>'
                         '<line x1="10" y1="9" x2="14" y2="9"/>'),
    "tag":           ('<path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/>'
                      '<line x1="7" y1="7" x2="7.01" y2="7"/>'),
    "boxes":         ('<path d="M2.97 12.92A2 2 0 0 0 2 14.63v3.24a2 2 0 0 0 .97 1.71l3 1.8a2 2 0 0 0 2.06 0L12 19v-5.5l-5-3-4.03 2.42Z"/>'
                      '<path d="m7 16.5-4.74-2.85"/><path d="m7 16.5 5-3"/>'
                      '<path d="M7 16.5v5.17"/>'
                      '<path d="M12 13.5V19l3.97 2.38a2 2 0 0 0 2.06 0l3-1.8a2 2 0 0 0 .97-1.71v-3.24a2 2 0 0 0-.97-1.71L17 10.5l-5 3Z"/>'
                      '<path d="M7.97 4.42A2 2 0 0 0 7 6.13v3.24a2 2 0 0 0 .97 1.71L11 12.88a2 2 0 0 0 2.06 0L17 10.5V5l-5-3-4.03 2.42Z"/>'),

    # ctxbar plot-type icons:
    "bar-chart-3":   ('<path d="M3 3v18h18"/>'
                      '<path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>'),
    "scatter-chart": ('<path d="M3 3v18h18"/>'
                      '<circle cx="7.5" cy="14.5" r="1.2"/><circle cx="11" cy="9.5" r="1.2"/>'
                      '<circle cx="14.5" cy="12" r="1.2"/><circle cx="17.5" cy="6.5" r="1.2"/>'
                      '<circle cx="9" cy="17" r="1.2"/>'),
    "bar-chart-horizontal": ('<path d="M3 3v18h18"/>'
                             '<line x1="7" y1="7" x2="11" y2="7"/>'
                             '<line x1="7" y1="12" x2="17" y2="12"/>'
                             '<line x1="7" y1="17" x2="14" y2="17"/>'),
    "axis":          ('<path d="M4 3v18h18"/>'
                      '<line x1="4" y1="8" x2="6" y2="8"/>'
                      '<line x1="4" y1="13" x2="6" y2="13"/>'
                      '<line x1="4" y1="18" x2="6" y2="18"/>'
                      '<line x1="9" y1="19" x2="9" y2="21"/>'
                      '<line x1="14" y1="19" x2="14" y2="21"/>'
                      '<line x1="19" y1="19" x2="19" y2="21"/>'),
    "grid-3x3":      ('<rect x="3" y="3" width="18" height="18" rx="2"/>'
                      '<line x1="3" y1="9" x2="21" y2="9"/>'
                      '<line x1="3" y1="15" x2="21" y2="15"/>'
                      '<line x1="9" y1="3" x2="9" y2="21"/>'
                      '<line x1="15" y1="3" x2="15" y2="21"/>'),

    # Titlebar / statusbar / shell:
    "refresh-cw":    ('<polyline points="23 4 23 10 17 10"/>'
                      '<polyline points="1 20 1 14 7 14"/>'
                      '<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/>'
                      '<path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/>'),
    "rotate-ccw":    ('<polyline points="1 4 1 10 7 10"/>'
                      '<path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>'),
    "folder-open":   ('<path d="M6 14l1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.55 6A2 2 0 0 1 18.46 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.93a2 2 0 0 1 1.66.9l.82 1.2a2 2 0 0 0 1.66.9H18a2 2 0 0 1 2 2v2"/>'),
    "panel-right-close": ('<rect x="3" y="3" width="18" height="18" rx="2"/>'
                          '<line x1="15" y1="3" x2="15" y2="21"/>'
                          '<polyline points="10 9 13 12 10 15"/>'),
    "panel-right-open":  ('<rect x="3" y="3" width="18" height="18" rx="2"/>'
                          '<line x1="15" y1="3" x2="15" y2="21"/>'
                          '<polyline points="13 9 10 12 13 15"/>'),
    "eye":           ('<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>'
                      '<circle cx="12" cy="12" r="3"/>'),
    "sliders-horizontal": ('<line x1="21" y1="4" x2="14" y2="4"/><line x1="10" y1="4" x2="3" y2="4"/>'
                           '<line x1="21" y1="12" x2="12" y2="12"/><line x1="8" y1="12" x2="3" y2="12"/>'
                           '<line x1="21" y1="20" x2="16" y2="20"/><line x1="12" y1="20" x2="3" y2="20"/>'
                           '<line x1="14" y1="2" x2="14" y2="6"/><line x1="8" y1="10" x2="8" y2="14"/>'
                           '<line x1="16" y1="18" x2="16" y2="22"/>'),
    "terminal-square": ('<rect x="3" y="3" width="18" height="18" rx="2"/>'
                        '<polyline points="7 11 10 14 7 17"/>'
                        '<line x1="13" y1="17" x2="17" y2="17"/>'),
    "chevron-up":    '<polyline points="18 15 12 9 6 15"/>',
    "moon":          '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "sun":           ('<circle cx="12" cy="12" r="5"/>'
                      '<line x1="12" y1="1" x2="12" y2="3"/>'
                      '<line x1="12" y1="21" x2="12" y2="23"/>'
                      '<line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>'
                      '<line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>'
                      '<line x1="1" y1="12" x2="3" y2="12"/>'
                      '<line x1="21" y1="12" x2="23" y2="12"/>'
                      '<line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>'
                      '<line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>'),

    # Plate / quick-select:
    "check-check":   ('<polyline points="3 13 8 18 11 15"/>'
                      '<polyline points="14 11 18 7 22 11"/>'),
    "flip-horizontal": ('<path d="M3 7V5a2 2 0 0 1 2-2h2"/>'
                        '<path d="M17 3h2a2 2 0 0 1 2 2v2"/>'
                        '<path d="M21 17v2a2 2 0 0 1-2 2h-2"/>'
                        '<path d="M7 21H5a2 2 0 0 1-2-2v-2"/>'
                        '<line x1="12" y1="3" x2="12" y2="21"/>'),
    "mouse-pointer-click": ('<path d="M9 9l5 12 1.8-5.2L21 14z"/>'
                            '<path d="M7.2 2.2 8 5.1"/><path d="M5.1 8 2.2 7.2"/>'),
    # Spatial rail glyph — four dots connected by lines, reads as
    # neighbour graph / colony framework.
    "waypoints":     ('<circle cx="12" cy="4.5" r="2.5"/>'
                      '<path d="m10.2 6.3-3.9 3.9"/>'
                      '<circle cx="4.5" cy="12" r="2.5"/>'
                      '<path d="M7 12h10"/>'
                      '<circle cx="19.5" cy="12" r="2.5"/>'
                      '<path d="m13.8 17.7 3.9-3.9"/>'
                      '<circle cx="12" cy="19.5" r="2.5"/>'),
}

AVAILABLE = tuple(sorted(_GLYPHS))

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

_PIXMAP_CACHE: dict[tuple, QPixmap] = {}


def _resolve_color(color) -> str:
    """Accept a hex string, a ``theme.Colors`` attribute name, or anything
    QColor understands; return a ``#rrggbb`` string for the SVG."""
    if isinstance(color, str) and not color.startswith("#") and hasattr(theme.Colors, color):
        color = getattr(theme.Colors, color)
    from PySide6.QtGui import QColor
    c = QColor(color)
    return c.name() if c.isValid() else theme.Colors.text_primary


def has_glyph(name: str) -> bool:
    """Whether *name* is a real glyph rather than one that would fall back.

    ``make_pixmap`` substitutes ``alert-triangle`` for an unknown name, which
    renders perfectly well and is indistinguishable from a deliberate warning
    icon — so a typo ships as a warning triangle beside an otherwise healthy
    row. Callers that pick glyph names from a table can assert against this
    instead of eyeballing the result.
    """
    return name in _GLYPHS


def make_pixmap(name: str, color, size_px: int, dpr: float = 1.0) -> QPixmap:
    """Render glyph *name* at *size_px* logical px in *color*, dpr-aware."""
    hexc = _resolve_color(color)
    key = (name, hexc, int(size_px), round(float(dpr), 3))
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached
    body = _GLYPHS.get(name, _GLYPHS["alert-triangle"])
    svg = _SVG_TEMPLATE.format(color=hexc, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    px = max(1, int(round(size_px * dpr)))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(p)
    p.end()
    pm.setDevicePixelRatio(float(dpr))
    _PIXMAP_CACHE[key] = pm
    return pm


def make_icon(name: str, size_px: int = 16, *,
              normal="text_secondary", active="text_primary",
              checked="accent", disabled="text_faint", dpr: float = 1.0) -> QIcon:
    """A ``QIcon`` with state-dependent token colors:

    Normal/Off → *normal*, Active (hover) → *active*, On (checked) → *checked*,
    Disabled → *disabled*.
    """
    icon = QIcon()
    icon.addPixmap(make_pixmap(name, normal, size_px, dpr), QIcon.Normal, QIcon.Off)
    icon.addPixmap(make_pixmap(name, active, size_px, dpr), QIcon.Active, QIcon.Off)
    icon.addPixmap(make_pixmap(name, checked, size_px, dpr), QIcon.Normal, QIcon.On)
    icon.addPixmap(make_pixmap(name, checked, size_px, dpr), QIcon.Active, QIcon.On)
    icon.addPixmap(make_pixmap(name, checked, size_px, dpr), QIcon.Selected, QIcon.On)
    icon.addPixmap(make_pixmap(name, disabled, size_px, dpr), QIcon.Disabled, QIcon.Off)
    icon.addPixmap(make_pixmap(name, disabled, size_px, dpr), QIcon.Disabled, QIcon.On)
    return icon


if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    host = QWidget()
    host.setWindowTitle("icons — preview")
    outer = QVBoxLayout(host)
    pad = theme.Spacing.lg
    outer.setContentsMargins(pad, pad, pad, pad)
    title = QLabel("Inline icon set")
    title.setObjectName("Title")
    outer.addWidget(title)

    grid_host = QWidget()
    grid = QGridLayout(grid_host)
    grid.setSpacing(theme.Spacing.lg)
    dpr = host.devicePixelRatioF()
    for i, name in enumerate(AVAILABLE):
        cell = QVBoxLayout()
        glyph = QLabel()
        glyph.setPixmap(make_pixmap(name, "text_primary", 20, dpr))
        glyph.setAlignment(Qt.AlignCenter)
        cap = QLabel(name)
        cap.setObjectName("Caption")
        cap.setAlignment(Qt.AlignCenter)
        cell.addWidget(glyph)
        cell.addWidget(cap)
        w = QWidget()
        w.setLayout(cell)
        grid.addWidget(w, i // 5, i % 5)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(grid_host)
    outer.addWidget(scroll, 1)

    host.resize(520, 420)
    host.show()
    _sys.exit(app.exec())
