"""The Analyze pane's output half: the log view, and how a run's outcome
reaches the screen.

Split out of ``analyze_tab_form.py`` and ``analyze_tab.py`` for their line
budgets, and because these members are one job that is neither form-building
nor run-driving. The form gathers the settings a run is launched with, the run
mixins start it and drain its queue; this shows what the run then said.

* ``_build_log`` — the "Pipeline Output" band (the raw-log toggle and Clear)
  over the log view itself.
* ``_on_toggle_raw_log`` — reveal or hide that view. It is hidden at build.
* ``force_raw_log_open`` / ``restore_raw_log`` — open that view on the user's
  behalf when a run fails, since the sentence explaining why is written here
  and nowhere else they can see, then hand the toggle back at the next run
  unless they have since made the choice their own.
* ``_surface_run_failure`` — the whole outcome path: the dashboard's failed or
  cancelled state, the raw log, and unfolding whichever form section the
  message names.
* ``_refresh_last_run_summary`` — the idle "Last run ·" card. Its only test is
  that ``pipeline_info.json`` exists, and the pipeline writes that before any
  image work, so the caller must gate it on the run having actually succeeded.

They bind to ``self`` — the host is ``AnalyzeTab``, and they reach its runner,
dashboard and collapsible sections through it.
"""

from __future__ import annotations

import logging
import sys as _sys

from PySide6.QtWidgets import (
    QCheckBox, QLabel, QPushButton, QTextEdit, QWidget,
)

from well_viewer.core.safe import swallowed
from well_viewer.core.ui_helpers import build_hline_separator
from widgets.run_dashboard import RunDashboard


#: Longest failure text the dashboard headline takes before it is cut back to
#: its first sentence. The headline is one line beside a progress plate; the
#: disk refusal alone runs to ~700 characters (two volumes, two figures, two
#: remedies), and pasting that into a wrapping label grew the dashboard by
#: 70 px and pushed the plate off screen. The whole text stays in the log and
#: on the tooltip, so the cut costs nothing.
_HEADLINE_MAX_CHARS = 160


def _headline_of(message: str) -> str:
    """The one-line form of a failure message: its first sentence, capped.

    Sentence-first rather than a blunt truncation because these messages are
    written to lead with what happened and follow with what to do about it, so
    the first sentence is the part a headline wants.
    """
    text = " ".join(str(message or "").split())
    if not text:
        return ""
    head, sep, _rest = text.partition(". ")
    first = (head + sep).strip() if sep else text
    if len(first) <= _HEADLINE_MAX_CHARS:
        return first
    return first[: _HEADLINE_MAX_CHARS - 1].rstrip() + "…"


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

        self._log_sep = build_hline_separator(parent)
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
        if not getattr(self, "_forcing_raw_log", False):
            # Anything that is not ``force_raw_log_open`` below is the user
            # working the toggle, and their choice outranks ours from here on.
            self._raw_log_forced = False
        for w in (getattr(self, "_log", None), getattr(self, "_log_sep", None)):
            if w is not None:
                w.setVisible(bool(show))

    def force_raw_log_open(self) -> bool:
        """Reveal the raw log on the user's behalf, remembering that we did.

        Returns True when the toggle actually moved. A failure writes its
        reason into the log view and nowhere else, so opening the view is the
        only way that reason reaches the screen — but the toggle is a setting
        the user chose, so the change is recorded rather than kept. The next
        run calls ``restore_raw_log`` to hand it back.
        """
        cb = getattr(self, "_raw_log_cb", None)
        if cb is None or cb.isChecked():
            return False
        self._raw_log_forced = True
        self._forcing_raw_log = True
        try:
            cb.setChecked(True)
        finally:
            self._forcing_raw_log = False
        return True

    def restore_raw_log(self) -> None:
        """Undo a ``force_raw_log_open`` the user has not since overruled."""
        if not getattr(self, "_raw_log_forced", False):
            return
        self._raw_log_forced = False
        cb = getattr(self, "_raw_log_cb", None)
        if cb is not None and cb.isChecked():
            cb.setChecked(False)

    def _surface_run_failure(self, message: str) -> None:
        """Put an ended-badly run where the user is already looking.

        The reason a run stopped reaches exactly one place on its own: a red
        line in ``self._log``, a QTextEdit hidden behind the "Show raw log"
        toggle and off by default. So a pipeline that refused to start — the
        disk preflight exiting before the first well, say — is otherwise
        indistinguishable from a run that was never clicked: progress stops at
        Grouped, the Run button returns, nothing states a reason.

        **A cancel is not a failure.** ``stop()`` and ``suspend()`` both end the
        subprocess by signal, so both arrive here as an ``("error", …)`` event
        carrying a signal exit code — ``PipelineRunner.was_stopped``'s own
        docstring says the three are indistinguishable from outside, which is
        why it exists. Painting a deliberate Stop red and quoting whatever line
        the pipeline happened to be on would be a worse lie than saying nothing.
        The dashboard's amber ``set_cancelled`` is the state for those, and it
        is what every Datasets tool branches to.

        The headline takes a *short* form. This message can run to hundreds of
        characters — the disk refusal names two volumes, two figures and two
        remedies — and a one-line label is not where that belongs: the log has
        the whole of it, and so does the tooltip.

        Neither surface is allowed to break the log drain — a widget fault here
        would abort ``_poll_log``'s loop and freeze the live log for the rest of
        the session — so both are guarded. A *silent* guard would be this
        function's own bug reintroduced inside it, so it logs at WARNING rather
        than the default DEBUG. The caller writes ``message`` into the log view
        before calling here, so the reason survives either fault; what is lost
        is only the surfacing.
        """
        text = str(message).strip()
        stopped = bool(getattr(self._runner, "was_stopped", False))
        suspended = bool(getattr(self._runner, "was_suspended", False))
        try:
            if suspended:
                self._dashboard.set_cancelled("suspended; resume to finish")
            elif stopped:
                self._dashboard.set_cancelled("stopped")
            else:
                self._dashboard.set_failed(_headline_of(text))
                self._dashboard.setToolTip(text)
        except Exception:
            swallowed("could not put the run outcome on the dashboard",
                      level=logging.WARNING)
        if stopped or suspended:
            # The user asked for this and knows why; opening a log pane they had
            # closed would be the app arguing with them.
            return
        try:
            self.force_raw_log_open()
        except Exception:
            swallowed("could not reveal the raw log after a run failure",
                      level=logging.WARNING)
        # A refusal that names a control has to leave that control on screen.
        # The disk check's remedies live in sections that fold by default, and
        # telling someone to use a field they cannot see is the same dead end as
        # not telling them at all — the "a control something else just changed
        # on their behalf" case ``reveal_section`` exists for. Driven off the
        # message naming the section, so it unfolds what is relevant and nothing
        # else, and needs no list to be kept in step with the sections.
        lowered = text.lower()
        for title in list(getattr(self, "_collapsible_sections", {})):
            if title.lower() in lowered:
                try:
                    self.reveal_section(title)
                except Exception:
                    swallowed(f"could not unfold {title!r} after a failure",
                              level=logging.WARNING)
