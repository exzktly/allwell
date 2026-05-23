"""Bottom/status/log view builder (Qt port)."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

class _GUILogHandler(logging.Handler):
    """Routes logging records into a QTextEdit on the Qt main thread."""

    def __init__(self, widget: QTextEdit) -> None:
        super().__init__()
        self._w = widget

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = record.levelname
            color_map = {
                "ERROR":   "#DC2626",
                "WARNING": "#D97706",
                "INFO":    "#E2E8F0",
                "DEBUG":   "#94A3B8",
            }
            color = color_map.get(level, "#E2E8F0")
            html = f'<span style="color:{color};">{msg}</span>'
            # Marshal to the Qt main thread.
            QTimer.singleShot(0, lambda h=html: self._append(h))
        except Exception:
            self.handleError(record)

    def _append(self, html: str) -> None:
        try:
            self._w.append(html)
        except Exception:
            pass


def build_bottom(app) -> None:
    """Build the persistent status/log footer strip."""
    from well_viewer import runtime_app as rt

    # Root layout on app must accept a bottom strip — we place it as a child.
    bottom = QWidget(app)
    bottom.setObjectName("Sidebar")
    outer = QVBoxLayout(bottom)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    # Add to app's layout if it has one; otherwise runtime_app must
    # arrange layout. We assume app has a QVBoxLayout via its own builder.
    app_layout = app.layout()
    if app_layout is not None:
        app_layout.addWidget(bottom)

    top_sep = QFrame(bottom)
    top_sep.setFrameShape(QFrame.HLine)
    top_sep.setFixedHeight(1)
    outer.addWidget(top_sep)

    # Status row
    status_row = QWidget(bottom)
    sr_l = QHBoxLayout(status_row)
    sr_l.setContentsMargins(4, 2, 4, 2)
    outer.addWidget(status_row)

    app._status_lbl = QLabel("Ready.", status_row)
    app._status_lbl.setObjectName("Muted")
    sr_l.addWidget(app._status_lbl, 1)

    app._progress_bar = QProgressBar(status_row)
    app._progress_bar.setOrientation(Qt.Horizontal)
    app._progress_bar.setFixedWidth(220)
    app._progress_bar.setRange(0, 100)
    app._progress_bar.setValue(0)
    app._progress_bar.hide()
    sr_l.addWidget(app._progress_bar)

    app._log_btn = QPushButton("Log \u25b2", status_row)
    app._log_btn.setProperty("variant", "secondary")
    app._log_btn.clicked.connect(lambda _=False: app._toggle_log())
    sr_l.addWidget(app._log_btn)

    # Log frame (hidden initially)
    app._log_frame = QWidget(bottom)
    app._log_frame.setFixedHeight(160)
    lf_l = QVBoxLayout(app._log_frame)
    lf_l.setContentsMargins(6, 4, 6, 2)
    outer.addWidget(app._log_frame)

    log_hdr = QWidget(app._log_frame)
    lh_l = QHBoxLayout(log_hdr)
    lh_l.setContentsMargins(0, 0, 0, 2)
    lf_l.addWidget(log_hdr)
    hdr_lbl = QLabel("LOG", log_hdr)
    hdr_lbl.setProperty("role", "section")
    lh_l.addWidget(hdr_lbl)
    lh_l.addStretch(1)
    clear_btn = QPushButton("Clear", log_hdr)
    clear_btn.setProperty("variant", "secondary")
    clear_btn.clicked.connect(lambda _=False: app._clear_log())
    lh_l.addWidget(clear_btn)

    app._log_text = QTextEdit(app._log_frame)
    app._log_text.setReadOnly(True)
    app._log_text.setLineWrapMode(QTextEdit.NoWrap)
    lf_l.addWidget(app._log_text, 1)

    app._log_frame.hide()
    app._log_visible = False

    handler = _GUILogHandler(app._log_text)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S"))
    handler.setLevel(logging.DEBUG)
    rt._logger.addHandler(handler)
    rt._logger.setLevel(logging.DEBUG)
    rt._logger.propagate = False
