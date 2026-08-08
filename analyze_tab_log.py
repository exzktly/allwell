"""The Analyze pane's output half: the log view and the last-run summary.

Split out of ``analyze_tab_form.py`` for its line budget, and because these
three members are one job that is not form-building. The form gathers the
settings a run is launched with; this shows what the run then said.

* ``_build_log`` — the "Pipeline Output" band (the raw-log toggle and Clear)
  over the log view itself.
* ``_on_toggle_raw_log`` — reveal or hide that view. It is hidden at build,
  which is why ``AnalyzeTab._surface_run_failure`` opens it on a failure: the
  sentence explaining why a run stopped is written here and nowhere else the
  user can see.
* ``_refresh_last_run_summary`` — the idle "Last run ·" card. Its only test is
  that ``pipeline_info.json`` exists, and the pipeline writes that before any
  image work, so the caller must gate it on the run having actually succeeded.

They bind to ``self`` exactly as before — a behaviour-preserving relocation.
"""

from __future__ import annotations

import sys as _sys

from PySide6.QtWidgets import (
    QCheckBox, QFrame, QLabel, QPushButton, QTextEdit, QWidget,
)

from widgets.run_dashboard import RunDashboard


class AnalyzeLogPaneMixin:
    """The Analyze pane's log view + last-run summary."""

    def _refresh_last_run_summary(self) -> None:
        """Populate the idle last-run card (C4) from the most recent run's
        pipeline_info.json. Hidden when no previous run is available."""
        card = getattr(self, "_last_run_card", None)
        if card is None:
            return
        out = self._last_output_dir  # a @property (Path | None)
        info_path = (out / "pipeline_info.json") if out else None
        if not info_path or not info_path.exists():
            card.setVisible(False)
            return
        try:
            import json
            info = json.loads(info_path.read_text())
        except Exception:
            card.setVisible(False)
            return
        method = str(info.get("segmentation_method", "—")).replace("_", " ")
        fluors = ", ".join(info.get("fluor_tokens", []) or []) or "—"
        n_tp = len(info.get("available_timepoints", []) or [])
        card.setText(
            f"Last run · {out.name}\n"
            f"Method: {method}   ·   Channels: {fluors}   ·   {n_tp} timepoint(s)"
        )
        card.setVisible(True)

    def _build_log(self, parent: QWidget) -> None:
        # The same control band every Review page carries, so switching modes
        # never moves the first row: the pane's title is scope, the raw-log
        # toggle and Clear are actions.
        from widgets.page_body import build_page_body
        band, body = build_page_body(parent)
        rl = body.layout()
        rl.setSpacing(0)

        title = QLabel("Pipeline Output", band)
        band.add_scope("", title)
        # F4: the dashboard above IS the summary view; the verbose text log is
        # off by default and revealed on demand, so progress reads cleanly.
        self._raw_log_cb = QCheckBox("Show raw log", band)
        self._raw_log_cb.setToolTip("Show the full per-image pipeline log beneath the progress dashboard")
        self._raw_log_cb.toggled.connect(self._on_toggle_raw_log)
        band.add_action(self._raw_log_cb)
        clear_btn = QPushButton("Clear", band)
        clear_btn.setProperty("variant", "secondary")
        clear_btn.clicked.connect(self._clear_log)
        band.add_action(clear_btn)
        # No separator under the band — ``#ControlBand`` paints its own
        # border-bottom hairline, so the rule that sat here was a second one.

        # Idle last-run summary (C4): a compact readout of the most recent run
        # so the pane isn't blank when nothing's running. Hidden during a run
        # and when there's no previous run to describe.
        self._last_run_card = QLabel("", parent)
        self._last_run_card.setObjectName("Muted")
        self._last_run_card.setWordWrap(True)
        self._last_run_card.setContentsMargins(12, 6, 12, 6)
        self._last_run_card.setVisible(False)
        rl.addWidget(self._last_run_card)
        self._refresh_last_run_summary()

        # Visual run dashboard — the single place run progress is reported:
        # status + ETA (header), overall % (headline + bar), per-stage rail and
        # the live per-well plate map. The raw log sits beneath it.
        self._dashboard = RunDashboard(parent)
        rl.addWidget(self._dashboard)

        self._log_sep = QFrame(parent)
        self._log_sep.setFrameShape(QFrame.HLine)
        self._log_sep.setObjectName("HRule")
        rl.addWidget(self._log_sep)

        self._log = QTextEdit(parent)
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QTextEdit.NoWrap)
        # Cap the QTextDocument so a multi-hour pipeline can't grow it to
        # hundreds of megabytes. Older lines drop off the top once we hit
        # the cap; the ring buffer in allwell captures everything for
        # the help-drawer log tab anyway.
        self._log.document().setMaximumBlockCount(10000)
        font = self._log.font()
        font.setFamily("Menlo" if _sys.platform == "darwin" else "Consolas")
        font.setPointSize(9)
        self._log.setFont(font)
        rl.addWidget(self._log, 1)

        # F4: raw log hidden until the user asks for it (dashboard is the summary).
        self._on_toggle_raw_log(False)

    def _on_toggle_raw_log(self, show: bool) -> None:
        """Show/hide the verbose text log (F4). The progress dashboard stays
        visible either way; this only toggles the raw per-image stream."""
        for w in (getattr(self, "_log", None), getattr(self, "_log_sep", None)):
            if w is not None:
                w.setVisible(bool(show))
