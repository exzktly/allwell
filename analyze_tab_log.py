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
* ``_refresh_last_run_summary`` — the idle "Last run ·" card. It shows whenever
  ``pipeline_info.json`` exists, which the pipeline writes before any image
  work, so the caller still gates it on the run having actually succeeded. What
  the card *says* about the run now leans on the ``run_check`` block instead:
  that one is written after the last well, so a run which died early has no
  completeness line to show and the card says so rather than reporting a
  method and a channel list that were true before anything happened.

They bind to ``self`` — the host is ``AnalyzeTab``, and they reach its runner,
dashboard and collapsible sections through it.
"""

from __future__ import annotations

import logging
import sys as _sys

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QLabel, QPushButton, QTextEdit, QWidget,
)

import theme
from well_viewer.core.safe import swallowed
from well_viewer.core.ui_helpers import build_hline_separator
from widgets.run_dashboard import RunDashboard


#: Longest failure text the dashboard headline takes before it is cut back.
#: The headline is a wrapping label above the progress plate, so the cost of a
#: long message is vertical: measured on an 840 px dashboard, the disk refusal
#: at its full ~700 characters takes the label to 144 px and the dashboard to
#: 396 px; capped here it is 80 px and 332 px, against 316 px idle. So this is
#: not a one-liner — it is a few lines instead of a dozen, which is what keeps
#: the plate on screen. The whole text stays in the log and on the headline's
#: tooltip, so the cut costs nothing.
_HEADLINE_MAX_CHARS = 160


def _headline_of(message: str) -> str:
    """The short form of a failure message: its first sentence, capped.

    Sentence-first rather than a blunt truncation because these messages are
    written to lead with what happened and follow with what to do about it, so
    the first sentence is the part a headline wants.

    The disk refusal's own first sentence is longer than the cap, so the cap
    does apply to the message this exists for. It cuts at the last space before
    the limit rather than mid-token: the sentence ends in a parenthesised count
    of wells and FOV-groups, and a blunt cut landed inside it as
    ``(10 FOV-gro…``, which reads as a rendering fault rather than an elision.
    """
    text = " ".join(str(message or "").split())
    if not text:
        return ""
    head, sep, _rest = text.partition(". ")
    first = (head + sep).strip() if sep else text
    if len(first) <= _HEADLINE_MAX_CHARS:
        return first
    cut = first[: _HEADLINE_MAX_CHARS - 1]
    space = cut.rfind(" ")
    # Only honour the word boundary when one exists late enough to leave a
    # usable headline; a single 200-character token still has to be cut.
    if space >= _HEADLINE_MAX_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


def _completeness_line(info: dict) -> str:
    """What the last run's completeness record says, in one line.

    The card above it is built entirely from keys ``pipeline_info.json`` carries
    *before* any image is read, so every one of them is equally true of a run
    that died in its first five seconds. ``run_check`` is the only key written
    at the end, and it is what lets this card say something a failed run could
    not have produced.

    Absent means the run predates the check, so the line says exactly that
    rather than implying a shortfall. A run whose check was turned off says
    that instead — a different thing, with a different fix.
    """
    check = info.get("run_check")
    if not isinstance(check, dict):
        return "Completeness: not recorded for this run"
    if check.get("skipped"):
        return "Completeness: check was turned off for this run"
    totals = check.get("totals") or {}
    n_wells = int(totals.get("wells") or 0)
    complete = int(totals.get("complete") or 0)
    bits = [f"{complete}/{n_wells} well(s) complete"]
    if int(totals.get("partial") or 0):
        bits.append(f"{int(totals['partial'])} partial")
    if check.get("suspended"):
        bits.append("run was suspended")
    if check.get("partial_scan"):
        bits.append("check did not finish")
    return "Completeness: " + "   ·   ".join(bits)


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
            f"Method: {method}   ·   Channels: {fluors}   ·   {n_tp} timepoint(s)\n"
            f"{_completeness_line(info)}"
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

    @staticmethod
    def _log_color(tag: str) -> str:
        return {
            "INFO":    theme.Colors.text_primary,
            "WARNING": theme.Colors.warn,
            "ERROR":   theme.Colors.danger,
            "DONE":    theme.Colors.success,
            "CMD":     theme.Colors.text_muted,
        }.get(tag, theme.Colors.text_primary)

    def _log_line(self, text: str, tag: str = "INFO") -> None:
        """Append one line. See :meth:`_log_lines` — this is the batch of one."""
        self._log_lines([(text, tag)])

    def _log_lines(self, batch) -> None:
        """Append many ``(text, tag)`` lines in as few inserts as the colours allow.

        One insert per line is what a log pane naturally wants and what this did
        until a remote run produced thousands of lines a second. The scroll
        bookkeeping either side of the insert reads ``verticalScrollBar()
        .maximum()``, and on a ``NoWrap`` document that forces a full re-layout
        — so the cost of a line grew with the document under it: measured at
        3.4 ms per line into an empty log, 19.1 ms once three thousand lines
        were in it, 57 seconds for the batch as a whole. All of it on the GUI
        thread, which is why the window stopped responding to anything —
        including the "Show raw log" checkbox — for as long as the flood lasted.

        Batching does not make an insert cheaper; it makes the *layout* happen
        once instead of once per line. Consecutive lines sharing a colour go in
        as one insert, so a run of ordinary output costs a single one.
        """
        if not batch:
            return
        # Snapshot the user's scroll position before appending. Auto-
        # scroll-to-bottom only if they were already pinned there;
        # otherwise leave the viewport alone so a user scrolled up to
        # read an earlier line isn't yanked back every time a new line
        # arrives.
        vsb = self._log.verticalScrollBar()
        at_bottom = vsb.value() >= vsb.maximum() - 4  # 4-px tolerance

        # v5.4.7: compensate for ``setMaximumBlockCount(10000)``-driven
        # trimming. When a new line pushes the block count over the
        # cap, Qt removes blocks from the TOP. From the scrollbar's
        # view that shifts every following pixel up — a user scrolled
        # to read an earlier line still LOOKS like they're at the
        # same value() but the content under that pixel has moved.
        # Without an explicit compensation the viewport drifts toward
        # the bottom (toward newer lines) every time trimming fires,
        # which felt extra-broken once the rsync progress2 stream
        # made trimming common during a remote run. Solution:
        # snapshot block count before insert, count lines added,
        # derive how many blocks were trimmed, subtract that many
        # line-heights from the saved scroll value.
        old_value = vsb.value()
        doc = self._log.document()
        old_block_count = doc.blockCount()
        lines_added = sum(t.count("\n") for t, _ in batch if t)
        # An ``insertText`` of "foo\nbar" appends 1 hard newline +
        # bar (no trailing newline) → blockCount goes up by 1.
        # "foo\nbar\n" → up by 1. "foo" (no newline) → up by 0.
        # The text we receive always has a trailing "\n" (see
        # callers), so ``\n``-count == new blocks added.

        # Use a *fresh* QTextCursor for the insert — setting the active
        # text cursor (the old code) is what forced the viewport to
        # follow the caret. The doc-bound cursor leaves the visible
        # cursor / selection alone.
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.End)
        run: list[str] = []
        run_tag = batch[0][1]
        for text, tag in batch:
            if tag != run_tag and run:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(self._log_color(run_tag)))
                cursor.insertText("".join(run), fmt)
                run, run_tag = [], tag
            run_tag = tag if not run else run_tag
            run.append(text)
        if run:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self._log_color(run_tag)))
            cursor.insertText("".join(run), fmt)

        if at_bottom:
            vsb.setValue(vsb.maximum())
            return

        # User was scrolled up. Did setMaximumBlockCount trim
        # blocks from the top? If so, shift the saved scroll value
        # up by the corresponding pixel height so the user stays
        # anchored to the SAME content they were reading instead
        # of drifting toward the new lines at the bottom.
        new_block_count = doc.blockCount()
        blocks_trimmed = max(0, old_block_count + lines_added - new_block_count)
        if blocks_trimmed > 0:
            line_height = max(1, self._log.fontMetrics().lineSpacing())
            vsb.setValue(max(0, old_value - blocks_trimmed * line_height))
        else:
            # No trimming — explicitly restore the pre-insert value
            # in case Qt did anything implicit.
            vsb.setValue(old_value)

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
        # The state as it stood when the failure was *queued*, not now. The
        # live ``was_stopped`` / ``was_suspended`` would let a Stop clicked in
        # the tick between the event landing and this draining turn a genuine
        # failure amber and silent — the reported bug, reintroduced by a race.
        stopped, suspended = getattr(
            self._runner, "cancel_state_at_failure", (False, False))
        try:
            if suspended:
                self._dashboard.set_cancelled("suspended; resume to finish")
            elif stopped:
                self._dashboard.set_cancelled("stopped")
            else:
                self._dashboard.set_failed(_headline_of(text), tooltip=text)
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
