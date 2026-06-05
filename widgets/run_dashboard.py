"""Visual run dashboard for the Analyze pipeline.

A compact, at-a-glance view of a pipeline run that sits above the raw log:

* a **stage stepper** — Group → (Upload) → Process → (Download) → Finalize →
  Done — that lights up the current coarse phase;
* a **live plate map** — an 8×12 :class:`WellPlateSelector` (non-interactive)
  that colours each well amber while it is being processed and green once it is
  done (red on failure), so a long run shows real, per-well motion;
* a **headline + ETA** mirroring the well count / percent / ETA.

The widget is a pure view: the host (``AnalyzeTab``) drives it by calling the
small method API below from the same points that already update the progress
bar, so the dashboard never touches the pipeline or the log queue itself.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ui.theme import CLR_DANGER, CLR_SUCCESS, TXT_MUT, TXT_PRI, WARN
from widgets.well_plate_selector import WellPlateSelector

# Well colours on the live plate map.
_COL_ACTIVE = WARN          # amber — currently processing
_COL_DONE = CLR_SUCCESS     # green — finished
_COL_ERROR = CLR_DANGER     # red — failed

# Ordered coarse stages. The remote-only Upload/Download steps are dropped from
# the stepper for local runs (see :meth:`RunDashboard.reset`).
_STAGES_LOCAL = [("group", "Group"), ("process", "Process"),
                 ("finalize", "Finalize"), ("done", "Done")]
_STAGES_REMOTE = [("group", "Group"), ("upload", "Upload"), ("process", "Process"),
                  ("download", "Download"), ("finalize", "Finalize"), ("done", "Done")]


class RunDashboard(QWidget):
    """At-a-glance run view. Driven entirely by the host via the methods below."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RunDashboard")
        self._stages = _STAGES_LOCAL
        self._stage_key = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        # ── header (title + status + collapse toggle) ───────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title = QLabel("Run Progress", self)
        title.setStyleSheet(f"color:{TXT_PRI}; font-weight:600;")
        hdr.addWidget(title)
        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        hdr.addWidget(self._status)
        hdr.addStretch(1)
        self._toggle = QToolButton(self)
        self._toggle.setText("▾")
        self._toggle.setAutoRaise(True)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.setToolTip("Collapse / expand the run progress view")
        self._toggle.clicked.connect(self._on_toggle)
        hdr.addWidget(self._toggle)
        root.addLayout(hdr)

        # ── collapsible body ────────────────────────────────────────────────
        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)

        # stage stepper
        self._stepper = QWidget(self._body)
        self._stepper_lay = QHBoxLayout(self._stepper)
        self._stepper_lay.setContentsMargins(0, 0, 0, 0)
        self._stepper_lay.setSpacing(4)
        self._step_labels: dict[str, QLabel] = {}
        self._build_stepper()
        body.addWidget(self._stepper)

        # headline + ETA
        head = QHBoxLayout()
        head.setSpacing(8)
        self._headline = QLabel("Idle", self._body)
        self._headline.setStyleSheet(f"color:{TXT_PRI};")
        head.addWidget(self._headline)
        head.addStretch(1)
        self._eta = QLabel("", self._body)
        self._eta.setObjectName("Muted")
        head.addWidget(self._eta)
        body.addLayout(head)

        # live plate map (non-interactive)
        self._plate = WellPlateSelector(self._body)
        self._plate.setSelectionMode("passive")
        self._plate.setDragSelectEnabled(False)
        self._plate.setRowColumnSelectable(False)
        # Read-only status map — hide the All 96 / Invert / Clear selection row;
        # the wells are coloured by run progress, not picked by the user.
        self._plate.setActionsVisible(False)
        self._plate.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._plate.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._plate.setMaximumHeight(210)
        body.addWidget(self._plate, 0, Qt.AlignHCenter)

        root.addWidget(self._body)

    # ── host-facing API ─────────────────────────────────────────────────────
    def reset(self, *, remote: bool = False) -> None:
        """Clear the plate, reset the stepper, ready for a new run."""
        self._stages = _STAGES_REMOTE if remote else _STAGES_LOCAL
        self._stage_key = ""
        self._build_stepper()
        self._plate.clearWellColors()
        self._plate.setEnabledWells(None)
        self._headline.setText("Preparing…")
        self._eta.setText("")
        self._status.setText("")

    def set_stage(self, key: str) -> None:
        """Highlight coarse stage *key* (and mark earlier stages done)."""
        order = [k for k, _ in self._stages]
        if key not in order or key == self._stage_key:
            return                       # unknown (e.g. remote step on a local run)
        self._stage_key = key
        cur = order.index(key)
        for i, (k, _name) in enumerate(self._stages):
            lbl = self._step_labels.get(k)
            if lbl is None:
                continue
            if cur < 0:
                self._style_step(lbl, "pending")
            elif i < cur:
                self._style_step(lbl, "done")
            elif i == cur:
                self._style_step(lbl, "active")
            else:
                self._style_step(lbl, "pending")

    def set_status(self, text: str) -> None:
        self._status.setText(text or "")

    def set_headline(self, text: str) -> None:
        self._headline.setText(text or "")

    def set_eta(self, text: str) -> None:
        self._eta.setText(text or "")

    def well_active(self, well_id: str) -> None:
        self._plate.setWellColor(well_id, _COL_ACTIVE)

    def well_done(self, well_id: str) -> None:
        self._plate.setWellColor(well_id, _COL_DONE)

    def well_error(self, well_id: str) -> None:
        self._plate.setWellColor(well_id, _COL_ERROR)

    def mark_all_done(self) -> None:
        """On completion, flip any still-amber (active) wells to done."""
        self.set_stage("done")

    # ── internals ───────────────────────────────────────────────────────────
    def _build_stepper(self) -> None:
        # clear existing
        while self._stepper_lay.count():
            item = self._stepper_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._step_labels = {}
        for i, (key, name) in enumerate(self._stages):
            if i:
                arrow = QLabel("→", self._stepper)
                arrow.setObjectName("Muted")
                self._stepper_lay.addWidget(arrow)
            lbl = QLabel(name, self._stepper)
            self._style_step(lbl, "pending")
            self._step_labels[key] = lbl
            self._stepper_lay.addWidget(lbl)
        self._stepper_lay.addStretch(1)

    @staticmethod
    def _style_step(lbl: QLabel, state: str) -> None:
        if state == "done":
            lbl.setText("✓ " + lbl.text().lstrip("✓ "))
            lbl.setStyleSheet(f"color:{CLR_SUCCESS};")
        elif state == "active":
            lbl.setText(lbl.text().lstrip("✓ "))
            lbl.setStyleSheet(f"color:{WARN}; font-weight:700;")
        else:  # pending
            lbl.setText(lbl.text().lstrip("✓ "))
            lbl.setStyleSheet(f"color:{TXT_MUT};")

    def _on_toggle(self) -> None:
        show = self._toggle.isChecked()
        self._body.setVisible(show)
        self._toggle.setText("▾" if show else "▸")
