"""The status-header and raw-log furniture every Datasets tool page carries.

Each of the six tool pages ends the same way: a header row naming its output
section with a "Show raw log" checkbox on the right, then — once that checkbox
reveals it — a separator and a read-only monospace log. Between those two ends
sits whatever that tool actually reports with: a `ConcatDashboard`, a bare
`QProgressBar`, a `ValidateDashboard` above a findings list. The ends were
copy-pasted six times; the middle is genuinely per-tool.

So this mixin owns the ends only. `append_log` and `clear_log` were
byte-identical across all six, and `_on_toggle_raw_log` was identical in four —
the other two differed by a docstring and by one extra widget in an existing
``getattr`` chain, which is why **one implementation covers all six with no
overrides**: it checks ``_status``, ``_log_sep`` and ``_log_hdr``, and a page
that has no ``_log_hdr`` simply contributes ``None`` there.

`datasets/_common/` held only logic before this (containers, dataset paths,
member names, zip passthrough). The absence of a shared *view* layer is how the
six pages drifted apart in the first place, one small inconsistency at a time.

Mixed in ahead of `QWidget` on each page class, so `self` is the page.
"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# The cap every one of these logs already carried, matching Analyze's own raw
# log — a long run must not grow the document unbounded.
_MAX_LOG_BLOCKS = 10000

# What the checkbox does, for the five pages that never said. Only Concatenate
# explained itself before; the wording here is deliberately tool-agnostic, and
# a page with something more specific to say passes its own.
_DEFAULT_TOGGLE_TIP = "Show this tool's full line-by-line output below."


def hrule(parent: QWidget) -> QFrame:
    """The one-pixel divider each page defined for itself."""
    rule = QFrame(parent)
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("HRule")
    return rule


class LogPanelMixin:
    """Status-section header + raw-log tail, shared by the six tool pages."""

    def build_log_header(self, outer: QVBoxLayout, title: str, *,
                         clear: bool = True,
                         toggle_tooltip: str = _DEFAULT_TOGGLE_TIP) -> None:
        """The section header: *title*, a "Show raw log" toggle, and a rule.

        *clear* adds a Clear button beside the toggle. Validate passes
        ``False``: its Clear sits in the raw-log header instead, because in the
        findings header it looked like it did nothing — the log it actually
        wiped was usually hidden at the time.
        """
        hdr = QWidget(self)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel(title, hdr))
        hl.addStretch(1)

        # The dashboard below IS the summary view; the verbose text log is off
        # by default and revealed on demand. Analyze builds the same furniture
        # (analyze/log_pane.py::_build_log / _on_toggle_raw_log) but starts its
        # log open, and the difference is deliberate: a tool page here reports
        # a short, bounded job whose dashboard genuinely is the whole story,
        # where an Analyze run takes hours and says several things — why it
        # refused to start, which volume filled, where the errors/ folder is —
        # only in the log.
        self._raw_log_cb = QCheckBox("Show raw log", hdr)
        if toggle_tooltip:
            self._raw_log_cb.setToolTip(toggle_tooltip)
        self._raw_log_cb.toggled.connect(self._on_toggle_raw_log)
        hl.addWidget(self._raw_log_cb)

        if clear:
            clear_btn = QPushButton("Clear", hdr)
            clear_btn.setProperty("variant", "secondary")
            clear_btn.clicked.connect(self.clear_log)
            hl.addWidget(clear_btn)

        outer.addWidget(hdr)
        outer.addWidget(hrule(self))

    def build_log_tail(self, outer: QVBoxLayout, *, object_name: str = "",
                       placeholder: str = "Status will appear here.",
                       log_header: str = "") -> None:
        """The separator and the raw log itself, both hidden until revealed.

        *log_header*, when given, inserts a header row of its own between the
        separator and the log, carrying a Clear button — the shape Validate
        needs so its Clear unambiguously belongs to the log.
        """
        self._log_sep = hrule(self)
        outer.addWidget(self._log_sep)

        if log_header:
            self._log_hdr = QWidget(self)
            log_hl = QHBoxLayout(self._log_hdr)
            log_hl.setContentsMargins(12, 6, 12, 6)
            log_hl.addWidget(QLabel(log_header, self._log_hdr))
            log_hl.addStretch(1)
            self._clear_log_btn = QPushButton("Clear log", self._log_hdr)
            self._clear_log_btn.setProperty("variant", "secondary")
            self._clear_log_btn.clicked.connect(self.clear_log)
            log_hl.addWidget(self._clear_log_btn)
            outer.addWidget(self._log_hdr)

        self._status = QPlainTextEdit(self)
        if object_name:
            self._status.setObjectName(object_name)
        self._status.setReadOnly(True)
        self._status.document().setMaximumBlockCount(_MAX_LOG_BLOCKS)
        self._status.setFont(QFont("monospace"))
        self._status.setPlaceholderText(placeholder)
        outer.addWidget(self._status, 1)

        self._on_toggle_raw_log(False)

    # ── the three members that were byte-identical six times over ────────
    def append_log(self, text: str) -> None:
        self._status.appendPlainText(text)

    def log_text(self) -> str:
        """Everything in the raw log, for a caller that needs to read it back.

        The log is the page's only durable record of what a file turned out to
        contain, so a test — or a user copying it into a message — has to be
        able to get at it without reaching into the widget.
        """
        view = getattr(self, "_status", None)
        return view.toPlainText() if view is not None else ""

    def clear_log(self) -> None:
        self._status.clear()

    def _on_toggle_raw_log(self, show: bool) -> None:
        """Show or hide the verbose log. Whatever the page reports progress
        with — a dashboard, a progress bar — stays visible either way.

        ``_log_hdr`` exists only on pages that asked for a log header, so the
        ``getattr`` covers all six without any of them needing an override.
        """
        for name in ("_status", "_log_sep", "_log_hdr"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setVisible(bool(show))
