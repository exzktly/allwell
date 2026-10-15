"""The Slurm-job indicator beside the Analyze pane's *Run on:* selector.

A small server glyph: lit while the current run holds an open Slurm job,
dimmed while its job is queued for a node, hidden otherwise. Its tooltip names
the job and the node's specifications (``analyze.domain.slurm_job``).

It sits in the *Run on:* row because that is where the user chose a
scheduler-backed host, and it is the one place on the page that is on screen
both while the form is being edited and while a run is going.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from analyze.domain import slurm_job as sj
from ui import theme
from widgets.icons import make_pixmap

#: The glyph's size, matching the form's 16-px inline icons.
_ICON_PX = 16


class SlurmJobIndicator(QLabel):
    """Shows :class:`~analyze.domain.slurm_job.SlurmJob` state as an icon."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SlurmJobIndicator")
        self._job = sj.SlurmJob()
        self._render()

    @property
    def job(self) -> sj.SlurmJob:
        return self._job

    def on_sched(self, state: str, job_id: str) -> None:
        self._set(sj.on_sched(self._job, state, job_id))

    def on_slurm_job(self, what: str, specs: dict[str, str]) -> None:
        if what == "specs":
            self._set(sj.on_specs(self._job, specs))
        elif what == "closed":
            self._set(sj.on_closed(self._job))

    def reset(self) -> None:
        self._set(sj.SlurmJob())

    def _set(self, job: sj.SlurmJob) -> None:
        if job != self._job:
            self._job = job
            self._render()

    def _render(self) -> None:
        state = self._job.state
        self.setVisible(state != sj.NONE)
        color = (theme.Colors.success if state == sj.OPEN
                 else theme.Colors.text_muted)
        dpr = self.devicePixelRatioF() if self.isVisible() else 1.0
        self.setPixmap(make_pixmap("server", color, _ICON_PX, dpr))
        self.setToolTip(sj.tooltip(self._job))
        self.setAccessibleName(sj.tooltip(self._job).split("\n", 1)[0])
