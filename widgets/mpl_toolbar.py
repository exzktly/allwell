"""MplToolbar — a styled matplotlib navigation toolbar.

A slim ``QWidget`` row of :class:`~widgets.icon_button.IconButton`s grouped the
matplotlib way (home · back/forward · pan/zoom · save). Coordinates are surfaced
only as a discreet chip beside the cursor on hover (the persistent bottom-right
``x = … · y = …`` readout was removed). It drives a hidden
``NavigationToolbar2QT`` so we get matplotlib's real handlers behind our own UI.

Use it standalone over any ``FigureCanvasQTAgg`` (e.g. inside a hover overlay), or
let :class:`~widgets.plot_card.PlotCard` embed one for you.

API
---
* ``MplToolbar(canvas, parent=None, *, nav=None, owner=None)`` — ``canvas`` is the
  ``FigureCanvasQTAgg``; pass an existing ``NavigationToolbar2QT`` as ``nav`` (or
  one is created, hidden, parented to ``owner`` or ``parent``).
* ``nav`` — the underlying ``NavigationToolbar2QT`` (read-only).
* ``setCoordinatesVisible(bool)`` / ``coordinatesVisible()`` — retained as
  no-ops (the bottom-right coordinate label was removed).
* ``home()`` / ``back()`` / ``forward()`` / ``save()`` — proxy the handlers (so the
  toolbar can be driven externally too).

matplotlib is an existing project dependency; if it's missing the module still
imports (``MplToolbar`` becomes ``None``) so ``import widgets.*`` doesn't explode.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QWidget,
)

import theme
from widgets.icon_button import IconButton
from util.aw_safe import swallowed

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_F_W = 1

try:  # matplotlib is a project dependency; degrade gracefully if absent.
    import matplotlib
    matplotlib.use("QtAgg", force=False)
    from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as _NavToolbar
    _HAVE_MPL = True
except Exception:  # pragma: no cover
    _HAVE_MPL = False


class _HoverChip(QLabel):
    """Tiny frameless label that mirrors x / y at the cursor.

    Custom widget instead of QToolTip because QToolTip carries OS-default
    padding + font sizing that read as a full-blown notification when the
    intent is a discreet caption next to the pointer.
    """

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        c, t = theme.Colors, theme.Typography
        self.setStyleSheet(
            f"QLabel {{"
            f" background-color: {c.panel_elevated};"
            f" color: {c.text_secondary};"
            f" border: 1px solid {c.border_subtle};"
            f" border-radius: 3px;"
            f" padding: 1px 4px;"
            f" font-family: {t.family_mono};"
            f" font-size: {t.caption_size}px;"
            f" }}"
        )

    def show_at(self, pos: QPoint, text: str) -> None:
        self.setText(text)
        self.adjustSize()
        self.move(pos)
        if not self.isVisible():
            self.show()


def _axes_have_drawn_content(axes) -> bool:
    """True iff any axis in *axes* carries a user-drawn artist.

    Empty plots before the first dataset render still expose default Axes,
    so ``event.inaxes`` is truthy and the hover tooltip would pop on bare
    grid. Check for the actual collections matplotlib populates on draw.
    """
    for ax in axes or ():
        if (ax.lines or ax.patches or ax.collections
                or ax.images or ax.containers):
            return True
    return False


if _HAVE_MPL:

    class MplToolbar(QWidget):
        def __init__(self, canvas, parent=None, *, nav=None, owner=None) -> None:
            super().__init__(parent)
            self.setObjectName("MplToolbar")
            self.canvas = canvas
            self._nav = nav if nav is not None else _NavToolbar(
                canvas, owner or parent or self, coordinates=False)
            try:
                self._nav.hide()
            except Exception:
                swallowed()

            lay = QHBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(theme.Spacing.xs)

            self._home = IconButton("home", tooltip="Reset view")
            self._back = IconButton("arrow-left", tooltip="Back")
            self._fwd = IconButton("arrow-right", tooltip="Forward")
            self._pan = IconButton("move", tooltip="Pan", checkable=True)
            self._zoom = IconButton("zoom-in", tooltip="Zoom to rectangle", checkable=True)
            self._save = IconButton("download", tooltip="Save figure…")

            grp = QButtonGroup(self)
            grp.setExclusive(False)   # allow toggling both modes off
            grp.addButton(self._pan)
            grp.addButton(self._zoom)

            self._home.clicked.connect(self.home)
            self._back.clicked.connect(self.back)
            self._fwd.clicked.connect(self.forward)
            self._save.clicked.connect(self.save)
            self._pan.toggled.connect(self._on_pan_toggled)
            self._zoom.toggled.connect(self._on_zoom_toggled)

            for w in (self._home, self._back, self._fwd):
                lay.addWidget(w)
            lay.addWidget(self._sep())
            lay.addWidget(self._pan)
            lay.addWidget(self._zoom)
            lay.addWidget(self._sep())
            lay.addWidget(self._save)
            lay.addStretch(1)

            # Floating chip beside the cursor — lazily created on first show
            # so headless / pre-paint construction doesn't allocate it.
            self._chip: _HoverChip | None = None

            # Store the cids so we can mpl_disconnect on destroy — without
            # that the closures outlive the QLabel they update and emit
            # ``RuntimeError: wrapped C/C++ object … deleted`` in stderr
            # when the toolbar is destroyed but the canvas survives
            # (e.g. lazy tab rebuild).
            self._motion_cid = None
            self._leave_cid = None
            try:
                self._motion_cid = self.canvas.mpl_connect(
                    "motion_notify_event", self._on_mouse_move
                )
                self._leave_cid = self.canvas.mpl_connect(
                    "figure_leave_event", self._on_figure_leave
                )
            except Exception:
                swallowed()
            self.setStyleSheet(self._qss())

        def closeEvent(self, event):  # Qt naming
            self._detach_canvas()
            try:
                super().closeEvent(event)
            except Exception:
                swallowed()

        def deleteLater(self):  # Qt naming
            self._detach_canvas()
            super().deleteLater()

        def _detach_canvas(self) -> None:
            for attr in ("_motion_cid", "_leave_cid"):
                cid = getattr(self, attr, None)
                if cid is None:
                    continue
                setattr(self, attr, None)
                try:
                    self.canvas.mpl_disconnect(cid)
                except Exception:
                    swallowed()
            chip = getattr(self, "_chip", None)
            if chip is not None:
                self._chip = None
                try:
                    chip.deleteLater()
                except Exception:
                    swallowed()

        # ── proxy the matplotlib handlers ───────────────────────────────────
        @property
        def nav(self):
            return self._nav

        def home(self) -> None:
            self._nav.home()

        def back(self) -> None:
            self._nav.back()

        def forward(self) -> None:
            self._nav.forward()

        def save(self) -> None:
            self._nav.save_figure()

        # ── pan / zoom (NavigationToolbar2QT toggles these internally) ───────
        def _on_pan_toggled(self, on: bool) -> None:
            if on and self._zoom.isChecked():
                self._zoom.setChecked(False)
            if on != (getattr(self._nav, "mode", "") == "pan"):
                self._nav.pan()

        def _on_zoom_toggled(self, on: bool) -> None:
            if on and self._pan.isChecked():
                self._pan.setChecked(False)
            if on != (getattr(self._nav, "mode", "") == "zoom rect"):
                self._nav.zoom()

        # ── coordinate readout ──────────────────────────────────────────────
        # The persistent bottom-right ``x = … · y = …`` label was removed; the
        # cursor-following hover chip below is the only coordinate readout. These
        # remain as no-ops so the documented API doesn't break for any caller.
        def setCoordinatesVisible(self, visible: bool) -> None:
            pass

        def coordinatesVisible(self) -> bool:
            return False

        def _on_mouse_move(self, event) -> None:
            ax = event.inaxes
            if ax is None or event.xdata is None or event.ydata is None:
                self._hide_chip()
                return

            text = f"x = {event.xdata:.3g}   ·   y = {event.ydata:.3g}"

            # Don't pop a hover chip on an empty axis — before data is
            # loaded the canvas is blank and a caption on bare grid lines
            # is just noise. Walk the figure's axes (twins / cohabiting
            # subplots) so a stacked chart with one populated axis shows.
            if not _axes_have_drawn_content(event.canvas.figure.axes):
                self._hide_chip()
                return

            # QCursor.pos() is in device-independent pixels; matplotlib's
            # event.x / event.y mix physical and logical px on HiDPI, so a
            # manual canvas-relative mapping landed the chip way off on
            # retina displays.
            try:
                if self._chip is None:
                    self._chip = _HoverChip()
                # +4/+2 keeps the chip directly adjacent to the cursor
                # (the previous +14 plus the OS tooltip's internal padding
                # was reading as ~half an inch on retina screens).
                self._chip.show_at(QCursor.pos() + QPoint(4, 2), text)
            except Exception:
                swallowed()

        def _on_figure_leave(self, _event) -> None:
            """Hide the hover chip when the cursor leaves the canvas."""
            self._hide_chip()

        def _hide_chip(self) -> None:
            if self._chip is not None and self._chip.isVisible():
                self._chip.hide()

        # ── chrome ──────────────────────────────────────────────────────────
        def _sep(self) -> QFrame:
            f = QFrame(self)
            f.setObjectName("MplToolbarSep")
            f.setFrameShape(QFrame.VLine)
            f.setFixedWidth(_F_W)
            return f

        def _qss(self) -> str:
            c, t = theme.Colors, theme.Typography
            return f"""
            QWidget#MplToolbar {{ background: transparent; }}
            QFrame#MplToolbarSep {{ background-color: {c.border_subtle}; border: none; }}
            QLabel#MplToolbarCoords {{
                color: {c.text_muted};
                font-family: {t.family_mono};
                font-size: {t.small_size}px;
                background: transparent;
            }}
            """

else:  # pragma: no cover - matplotlib unavailable
    MplToolbar = None  # type: ignore


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget as _QW

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("MplToolbar — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)
    title = QLabel("MplToolbar")
    title.setObjectName("Title")
    lay.addWidget(title)

    if MplToolbar is None:
        lay.addWidget(QLabel("matplotlib is not installed — MplToolbar unavailable."))
    else:
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as _FC
        from matplotlib.figure import Figure as _F
        fig = _F(figsize=(5, 3), layout="constrained")
        ax = fig.add_subplot(111)
        import math
        xs = [i * 0.1 for i in range(120)]
        ax.plot(xs, [math.sin(x) for x in xs], linewidth=1.6)
        ax.set_xlabel("x"); ax.set_ylabel("sin x")
        canvas = _FC(fig)
        lay.addWidget(canvas, 1)
        lay.addWidget(MplToolbar(canvas))

    root.resize(640, 460)
    root.show()
    _sys.exit(app.exec())
