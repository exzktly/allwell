"""Bottom/status/log view builder (Qt port)."""

from __future__ import annotations

import logging

from ui import theme

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)
from well_viewer.core.ui_helpers import build_hline_separator
from well_viewer.core.worker_bridge import GuiLogRelay

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_LOG_FRAME_H = 160
_PROGRESS_BAR_W = 220

#: How many lines the log drawer keeps before dropping the oldest.
_LOG_MAX_BLOCKS = 5000


class _GUILogHandler(logging.Handler):
    """Routes logging records into a QTextEdit on the Qt main thread.

    This handler is attached to the ``well_viewer`` logger at DEBUG, and it is
    gated on nothing, so it runs for every record the app produces — including
    the ``well_viewer.swallowed`` breadcrumb that
    :func:`well_viewer.core.safe.swallowed` writes from inside an ``except``
    block. Plenty of those happen on worker threads, so ``emit`` is entered off
    the GUI thread as a matter of course and must never touch the widget
    directly. It hands the line to a :class:`~well_viewer.core.worker_bridge.
    GuiLogRelay`, whose queued signal is the app's one way across that boundary.

    Detaches itself the moment its widget is gone. A handler outlives the window
    it writes into — nothing removes it on teardown, and in a process that builds
    more than one window (the test suite does) the dead ones stay subscribed to
    the same logger. Left attached they are not merely inert: see :meth:`_append`.
    """

    def __init__(self, widget: QTextEdit) -> None:
        super().__init__()
        self._w = widget
        # Built here, so its thread affinity is whichever thread constructs the
        # handler — the GUI thread, since ``build_bottom`` runs there.
        self._relay = GuiLogRelay(self._append)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = record.levelname
            color_map = {
                "ERROR":   theme.Colors.danger,
                "WARNING": theme.Colors.warn,
                "INFO":    theme.Colors.text_secondary,
                "DEBUG":   theme.Colors.text_muted,
            }
            color = color_map.get(level, theme.Colors.text_secondary)
            html = f'<span style="color:{color};">{msg}</span>'
            # Marshal to the Qt main thread. Never a QTimer: see GuiLogRelay.
            self._relay.post(html)
        except Exception:
            self.handleError(record)

    def _append(self, html: str) -> None:
        try:
            self._w.append(html)
        except RuntimeError:
            # The QTextEdit's C++ half is gone. This handler can never work
            # again, so drop it from every logger holding it — otherwise each
            # record keeps fanning out to a widget that cannot receive it.
            #
            # Deliberately NOT swallowed(): that logs at DEBUG, and this failure
            # is *inside a log handler*. The record would propagate straight back
            # to the logger this handler is attached to, fail again here, and log
            # again — an unbounded loop that a redraw's processEvents() drains
            # forever. A logging handler must never log from its own failure path.
            self._detach()

    def _detach(self) -> None:
        """Remove this handler from any logger still holding a reference."""
        for logger in (logging.getLogger("well_viewer"), logging.getLogger()):
            if self in logger.handlers:
                logger.removeHandler(self)


def build_bottom(win) -> None:
    """Build the persistent status/log footer strip."""
    from well_viewer import runtime_app as rt

    # Root layout on win must accept a bottom strip — we place it as a child.
    bottom = QWidget(win)
    bottom.setObjectName("Sidebar")
    outer = QVBoxLayout(bottom)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    # Add to win's layout if it has one; otherwise runtime_app must
    # arrange layout. We assume win has a QVBoxLayout via its own builder.
    app_layout = win.layout()
    if app_layout is not None:
        app_layout.addWidget(bottom)

    # Expose the footer strip so an embedding shell (allwell) can hide it and
    # adopt the live status label + progress bar into a single bottom bar,
    # instead of stacking two "Ready." strips. Standalone WellViewerApp keeps
    # its own footer visible.
    win._status_footer = bottom

    outer.addWidget(build_hline_separator(bottom))

    # Status row
    status_row = QWidget(bottom)
    sr_l = QHBoxLayout(status_row)
    sr_l.setContentsMargins(4, 2, 4, 2)
    outer.addWidget(status_row)

    win._status_lbl = QLabel("Ready.", status_row)
    win._status_lbl.setObjectName("Muted")
    sr_l.addWidget(win._status_lbl, 1)

    # (The bottom-bar Threshold / Error-band chips were removed — those controls
    # now live in their own tab contexts, so the footer keeps only the status
    # readout, the progress bar, and the Log toggle.)

    # Register the shared status-bar progress bar through the WidgetRegistry so
    # features reach it via ``ctx.widgets.get_or_none("_progress_bar")`` (A8);
    # ``register`` also stamps ``win._progress_bar`` so the legacy readers
    # (ui_helpers.show_progress / step_progress) keep resolving unchanged.
    win._widget_registry.register("_progress_bar", QProgressBar(status_row))
    win._progress_bar.setOrientation(Qt.Horizontal)
    win._progress_bar.setFixedWidth(_PROGRESS_BAR_W)
    win._progress_bar.setRange(0, 100)
    win._progress_bar.setValue(0)
    win._progress_bar.hide()
    sr_l.addWidget(win._progress_bar)

    win._log_btn = QPushButton("Log \u25b4", status_row)
    win._log_btn.setProperty("variant", "secondary")
    win._log_btn.clicked.connect(lambda _=False: win._toggle_log())
    sr_l.addWidget(win._log_btn)

    # Log frame (hidden initially)
    win._log_frame = QWidget(bottom)
    win._log_frame.setFixedHeight(_LOG_FRAME_H)
    lf_l = QVBoxLayout(win._log_frame)
    lf_l.setContentsMargins(6, 4, 6, 2)
    outer.addWidget(win._log_frame)

    log_hdr = QWidget(win._log_frame)
    lh_l = QHBoxLayout(log_hdr)
    lh_l.setContentsMargins(0, 0, 0, 2)
    lf_l.addWidget(log_hdr)
    hdr_lbl = QLabel("LOG", log_hdr)
    hdr_lbl.setProperty("role", "section")
    lh_l.addWidget(hdr_lbl)
    lh_l.addStretch(1)
    clear_btn = QPushButton("Clear", log_hdr)
    clear_btn.setProperty("variant", "secondary")
    clear_btn.clicked.connect(lambda _=False: win._clear_log())
    lh_l.addWidget(clear_btn)

    win._log_text = QTextEdit(win._log_frame)
    win._log_text.setReadOnly(True)
    win._log_text.setLineWrapMode(QTextEdit.NoWrap)
    # Ring-buffer the drawer: the handler below logs at DEBUG, and a long session
    # (or one image-heavy load) emits enough records to grow the document without
    # bound, which costs memory and makes every later append slower. Oldest lines
    # drop off the top; the drawer is a tail, not an archive.
    win._log_text.document().setMaximumBlockCount(_LOG_MAX_BLOCKS)
    lf_l.addWidget(win._log_text, 1)

    win._log_frame.hide()
    win._log_visible = False

    handler = _GUILogHandler(win._log_text)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S"))
    handler.setLevel(logging.DEBUG)
    rt._logger.addHandler(handler)
    rt._logger.setLevel(logging.DEBUG)
    rt._logger.propagate = False
