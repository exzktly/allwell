"""Run-progress rendering for the Analyze pane.

The pipeline reports progress as a stream of events parsed out of its stdout by
:class:`services.pipeline_runner.ProgressTracker`; this module turns those
events into what the user sees — the dashboard headline, the overall bar, the
per-well plate map, and the ETA.

Held as a mixin rather than added to ``analyze_tab.py`` for the same reason
``AnalyzeSuspendMixin`` is: that file sits against its line budget, and the
rendering of run progress is a self-contained job with one entry point
(:meth:`AnalyzeProgressMixin._apply_progress_event`) rather than something the
tab's other behaviour reaches into.

The rule the whole module answers to: what it draws must never overstate what
the run achieved. A bar at 100% and a headline reading "N / N wells" are the
only signal the user has that a plate finished, so a count that can be reached
while wells are still outstanding is a lie the rest of the window cannot
correct. Hence the shortfall branches — a run that ends with fewer wells than
it announced says so in the headline and in the log, and a well announcing
completion twice is reported rather than folded into the count.
"""

from __future__ import annotations

import time as _time

from PySide6.QtCore import QTimer


class AnalyzeProgressMixin:
    """Progress-event rendering for ``AnalyzeTab``.

    Expects the host to provide ``_dashboard``, ``_log_line``, ``_well_total``
    / ``_well_done`` / ``_well_repeats``, and ``_pipeline_started_at``.
    """

    def _update_eta(self, done: int, total: int) -> None:
        # Remember the latest progress snapshot so the tick timer can
        # re-render the ETA against live ``_time.monotonic()`` between
        # well_done events.
        self._eta_done = int(done)
        self._eta_total = int(total)
        # Lock in a deadline at this event using the cumulative per-well
        # wall-clock rate. The tick timer between events just renders
        # ``deadline - now`` — never recomputing from elapsed, which would
        # make the displayed remaining grow while waiting for the next
        # well to complete.
        started = getattr(self, "_pipeline_started_at", None)
        if (
            started is not None
            and self._eta_done > 0
            and self._eta_total > 0
            and self._eta_done < self._eta_total
        ):
            elapsed = _time.monotonic() - started
            if elapsed > 0.0:
                per_well_rate = elapsed / self._eta_done
                remaining_wells = self._eta_total - self._eta_done
                self._eta_deadline = _time.monotonic() + remaining_wells * per_well_rate
        self._render_eta()
        # Start the 1-Hz tick timer the first time we get usable progress
        # so the label counts down even when no new well_done arrives.
        timer = getattr(self, "_eta_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(self._render_eta)
            self._eta_timer = timer
        if done > 0 and total > 0 and done < total and not timer.isActive():
            timer.start()

    def _render_eta(self) -> None:
        started = getattr(self, "_pipeline_started_at", None)
        done = int(getattr(self, "_eta_done", 0))
        total = int(getattr(self, "_eta_total", 0))
        if started is None:
            self._dashboard.set_eta("")
            return
        if total > 0 and done >= total:
            # All wells finished. The pipeline still has post-well work
            # (write pipeline_info.json, run the auto-threshold pass)
            # before the "finished" event fires; surface that state
            # explicitly so the user doesn't see a stale ETA / blank
            # while the progress bar sits at 100%.
            self._dashboard.set_eta("Finalizing…")
            return
        deadline = getattr(self, "_eta_deadline", None)

        def _fmt(secs: float) -> str:
            secs = int(round(max(0.0, secs)))
            if secs >= 3600:
                return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
            if secs >= 60:
                return f"{secs // 60}m{secs % 60:02d}s"
            return f"{secs}s"

        if deadline is None:
            # Pipeline is running but no well has completed yet — show a
            # placeholder so the user sees the timer is alive.
            self._dashboard.set_eta("ETA")
            return
        remaining = deadline - _time.monotonic()
        self._dashboard.set_eta(f"ETA {_fmt(remaining)}")

    def _stop_eta_timer(self) -> None:
        timer = getattr(self, "_eta_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        self._eta_done = 0
        self._eta_total = 0
        self._eta_deadline = None

    def _apply_grouping_event(self, kind: str, payload: object) -> None:
        """Render the grouping phase's own events onto the dashboard.

        ``zipper_start`` opens the Group stage with the well count it is about
        to cover; ``stage_total`` gives one rail row a denominator of its own,
        for a stage whose population differs from the run's (see
        :meth:`widgets.run_dashboard.RunDashboard.set_stage_total`) — on a
        remote run Group and Download span the whole plate while the stages
        between them only ever see the wells the upload split sent.
        """
        if kind == "zipper_start":
            n = payload or 96
            self._zipper_done = 0
            self._dashboard.set_total(n)
            self._dashboard.set_overall_range(n)
            self._dashboard.set_overall(0)
            self._dashboard.set_active_stage("group")
            self._dashboard.set_headline(f"Grouping 0 / {n} wells")
        elif kind == "stage_total":
            stage, total = payload  # type: ignore[misc]
            self._dashboard.set_stage_total(str(stage), int(total))

    def _apply_progress_event(self, kind: str, payload: object) -> None:
        """Render a progress event from :class:`ProgressTracker` to the UI."""
        if kind == "well_total":
            # Announced once at the start of the run, and again if more wells
            # finish than were announced (the tracker treats its total as a
            # floor). Carry the running count across rather than resetting to
            # zero, so a re-announcement corrects the denominator without
            # rewinding the bar.
            total = int(payload)  # type: ignore[arg-type]
            self._well_total = total
            done = min(self._well_done, total)
            self._dashboard.set_total(total)
            self._dashboard.set_overall_range(total)
            self._dashboard.set_overall(done)
            self._dashboard.set_headline(f"Processing {done} / {total} wells")
        elif kind == "well_done":
            done, total = payload  # type: ignore[misc]
            self._well_done = int(done)
            self._dashboard.set_overall(self._well_done)
            pct = int(self._well_done / max(1, total) * 100)
            self._update_eta(self._well_done, total)
            self._dashboard.set_headline(
                f"Processing {self._well_done} / {total} wells  ({pct}%)")
            # The auto-threshold pass runs *after* the last well — show
            # an explicit finalizing state so the user doesn't see the
            # progress bar pinned at 100% with nothing happening.
            if total > 0 and self._well_done >= total:
                self._dashboard.set_status("Finalizing…")
        elif kind == "well_repeat":
            # The same well announced completion twice. It is counted once, so
            # the headline stays honest; say what happened, because a repeat
            # means the well was processed (or skipped) a second time and that
            # is worth knowing — on a batched remote run it points at an input
            # zip that outlived its batch.
            self._well_repeats += 1
            self._log_line(
                f"[warn] {payload} reported completion more than once — "
                f"counted once. The well was processed or skipped again "
                f"(repeat {self._well_repeats} this run).\n",
                "WARNING",
            )
        elif kind == "stage":
            self._dashboard.set_active_stage(str(payload))
        elif kind == "well_stage":
            well, stage = payload  # type: ignore[misc]
            self._dashboard.set_well_stage(str(well), str(stage))
        elif kind == "well_fov":
            well, done, total = payload  # type: ignore[misc]
            self._dashboard.set_well_fov(str(well), int(done), int(total))
        elif kind == "well_xfer":
            well, direction, frac = payload  # type: ignore[misc]
            self._dashboard.set_well_transfer(str(well), str(direction), float(frac))
        elif kind == "status":
            # Descriptive finalize-phase status (auto-threshold / remote drain
            # / cleanup) so the post-well work doesn't read as a hang.
            self._dashboard.set_status(str(payload))
