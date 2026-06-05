"""Visual run dashboard for the Analyze pipeline.

An at-a-glance, per-well view of a pipeline run that sits above the raw log:

* a **stage rail** (left) — every lifecycle stage (Group → Upload → Process →
  Download → Threshold) with a live per-well ``done/total`` count + mini-bar, the
  active stage highlighted. Upload/Download appear only for remote runs;
* a **live plate map** (centre) — an 8×12 :class:`WellPlateSelector` coloured by
  each well's *furthest* lifecycle stage, with a glyph on the well(s) currently
  computing and on failures, and a per-well hover tooltip;
* a **legend** (right) explaining the colours.

The widget is a pure view: the host (``AnalyzeTab``) drives it from the same
progress events as the bar, via :meth:`reset`, :meth:`set_well_stage`,
:meth:`set_active_stage`, and the headline/eta/status setters.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)

from ui.theme import ACCENT, CLR_DANGER, CLR_SUCCESS, TXT_MUT, TXT_PRI, WARN
from widgets.well_plate_selector import WellPlateSelector

# Per-well completed milestones. Stages are tracked INDEPENDENTLY (not as a
# single "furthest" rank) because the order differs local vs remote: on a remote
# run the auto-threshold pass runs on the server *before* the local download, so
# "threshold" must not imply "download" (that made the Download count hit N/N
# before any file was fetched).
_LABELS = {"group": "grouped", "upload": "uploaded", "active": "processing",
           "process": "processed", "download": "fetched locally",
           "threshold": "thresholded", "error": "failed"}
_STAGE_KEYS = ("group", "upload", "active", "process", "threshold", "download")

# Plate colours.
_COL_PROCESSING = WARN          # amber — computing / queued
_COL_FETCHED = ACCENT           # blue — computed (remote: on server, awaiting fetch)
_COL_COMPLETE = CLR_SUCCESS     # green — done with data local
_COL_ERROR = CLR_DANGER         # red

# Rail rows in the order they actually happen. Remote: the server thresholds
# before the client downloads, so Download is the LAST (terminal, local) step.
_RAIL_LOCAL = [("group", "Group"), ("process", "Process"), ("threshold", "Threshold")]
_RAIL_REMOTE = [("group", "Group"), ("upload", "Upload"), ("process", "Process"),
                ("threshold", "Threshold"), ("download", "Download")]


def _canon(well_id: str) -> str:
    """Normalise a well token to ``A01`` form; '' if it isn't a well."""
    import re
    m = re.match(r"^([A-Ha-h])(\d{1,2})$", str(well_id).strip())
    return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else ""


def _dot(color: str, d: int = 11) -> QLabel:
    lbl = QLabel()
    lbl.setFixedSize(d, d)
    lbl.setStyleSheet(f"background:{color}; border-radius:{d // 2}px;")
    return lbl


class RunDashboard(QWidget):
    """At-a-glance per-well run view. Driven entirely by the host."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RunDashboard")
        self._remote = False
        self._total = 0
        self._done: dict[str, set] = {}          # stage -> set of wells completed
        self._active: set = set()                # wells currently processing
        self._errored: set = set()
        self._active_stage = ""
        self._rail: dict[str, tuple] = {}        # stage -> (dot,name,bar,count)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        # header: title + status + collapse toggle
        hdr = QHBoxLayout(); hdr.setSpacing(8)
        title = QLabel("Run Progress", self)
        title.setStyleSheet(f"color:{TXT_PRI}; font-weight:600;")
        hdr.addWidget(title)
        self._status = QLabel("", self); self._status.setObjectName("Muted")
        hdr.addWidget(self._status)
        hdr.addStretch(1)
        self._eta = QLabel("", self); self._eta.setObjectName("Muted")
        hdr.addWidget(self._eta)
        self._toggle = QToolButton(self)
        self._toggle.setText("▾"); self._toggle.setAutoRaise(True)
        self._toggle.setCheckable(True); self._toggle.setChecked(True)
        self._toggle.setToolTip("Collapse / expand the run progress view")
        self._toggle.clicked.connect(self._on_toggle)
        hdr.addWidget(self._toggle)
        root.addLayout(hdr)

        self._headline = QLabel("Idle", self)
        self._headline.setStyleSheet(f"color:{TXT_PRI};")
        root.addWidget(self._headline)

        # body: rail | plate | legend
        self._body = QWidget(self)
        body = QHBoxLayout(self._body)
        body.setContentsMargins(0, 4, 0, 0); body.setSpacing(14)

        self._rail_host = QWidget(self._body)
        self._rail_lay = QVBoxLayout(self._rail_host)
        self._rail_lay.setContentsMargins(0, 0, 0, 0); self._rail_lay.setSpacing(8)
        self._rail_host.setFixedWidth(238)
        body.addWidget(self._rail_host)

        d1 = QFrame(self._body); d1.setFrameShape(QFrame.VLine); d1.setObjectName("VRule")
        body.addWidget(d1)

        self._plate = WellPlateSelector(self._body)
        self._plate.setSelectionMode("passive")
        self._plate.setDragSelectEnabled(False)
        self._plate.setRowColumnSelectable(False)
        self._plate.setActionsVisible(False)
        # Paint coloured wells in the raised/lit style (not the dim recessed
        # tint) so run states read clearly on the status map.
        self._plate.setColoredWellsLit(True)
        self._plate.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._plate.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._plate.setMaximumHeight(210)
        self._plate.setWellTooltipProvider(self._well_tooltip)
        body.addWidget(self._plate, 1)

        d2 = QFrame(self._body); d2.setFrameShape(QFrame.VLine); d2.setObjectName("VRule")
        body.addWidget(d2)

        self._legend_host = QWidget(self._body)
        self._legend_lay = QVBoxLayout(self._legend_host)
        self._legend_lay.setContentsMargins(0, 0, 0, 0); self._legend_lay.setSpacing(8)
        self._legend_host.setFixedWidth(140)
        body.addWidget(self._legend_host)

        root.addWidget(self._body)
        self._build_rail(); self._build_legend()

    # ── host-facing API ─────────────────────────────────────────────────────
    def reset(self, *, remote: bool = False, total: int = 0) -> None:
        self._remote = remote
        self._total = total
        self._done = {}
        self._active = set()
        self._errored = set()
        self._active_stage = ""
        self._plate.clearWellColors()
        self._plate.clearWellMarkers()
        self._plate.setEnabledWells(None)
        self._build_rail(); self._build_legend()
        self._headline.setText("Preparing…")
        self._eta.setText(""); self._status.setText("")

    def set_total(self, total: int) -> None:
        self._total = int(total)
        self._refresh_rail()

    def set_well_stage(self, well_id: str, stage: str) -> None:
        """Record that *well_id* reached *stage* (the main driver). Each stage is
        tracked independently — reaching one does not imply any other."""
        w = _canon(well_id)
        if not w or stage not in _STAGE_KEYS and stage != "error":
            return
        if stage == "error":
            self._errored.add(w)
            self._active.discard(w)
        elif stage == "active":
            if w not in self._errored:
                self._active.add(w)
        else:
            self._done.setdefault(stage, set()).add(w)
            self._active.discard(w)
        self._plate.setWellColor(w, self._well_color(w))
        self._plate.setWellMarker(
            w, "✕" if w in self._errored
            else ("⚙" if w in self._active else None))
        self._refresh_rail()

    def set_active_stage(self, name: str) -> None:
        """Highlight the rail row for the coarse phase the run is in."""
        key = "threshold" if name == "finalize" else name
        if key == self._active_stage:
            return
        self._active_stage = key
        self._refresh_rail()

    def set_headline(self, text: str) -> None:
        self._headline.setText(text or "")

    def set_eta(self, text: str) -> None:
        self._eta.setText(text or "")

    def set_status(self, text: str) -> None:
        self._status.setText(text or "")

    # ── internals ───────────────────────────────────────────────────────────
    def _has(self, stage: str, w: str) -> bool:
        return w in self._done.get(stage, ())

    def _well_color(self, w: str) -> "str | None":
        """Plate colour for a well, honouring the remote vs local lifecycle.

        Green means "done with the data on your machine": for a remote run that
        is the *download*, not the (server-side) threshold; for a local run it is
        the threshold. Blue is "computed but the data isn't local yet"."""
        if w in self._errored:
            return _COL_ERROR
        if self._remote:
            if self._has("download", w):
                return _COL_COMPLETE                      # fetched locally — done
            if self._has("process", w) or self._has("threshold", w):
                return _COL_FETCHED                       # computed on server, awaiting fetch
            if w in self._active or self._has("upload", w) or self._has("group", w):
                return _COL_PROCESSING
            return None
        # local
        if self._has("threshold", w):
            return _COL_COMPLETE
        if self._has("process", w):
            return _COL_FETCHED
        if w in self._active or self._has("group", w):
            return _COL_PROCESSING
        return None

    def _count(self, stage: str) -> int:
        return len(self._done.get(stage, ()))

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)        # un-render immediately
                w.deleteLater()

    def _build_rail(self) -> None:
        self._clear(self._rail_lay)
        self._rail = {}
        head = QLabel("Stages", self._rail_host)
        head.setStyleSheet(f"color:{TXT_PRI}; font-weight:600;")
        self._rail_lay.addWidget(head)
        rows = _RAIL_REMOTE if self._remote else _RAIL_LOCAL
        for key, label in rows:
            row_w = QWidget(self._rail_host)
            row = QHBoxLayout(row_w); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(7)
            dot = _dot(TXT_MUT)
            row.addWidget(dot)
            nm = QLabel(label, row_w); nm.setFixedWidth(74)
            nm.setStyleSheet(f"color:{TXT_PRI};")
            row.addWidget(nm)
            bar = QProgressBar(row_w)
            bar.setRange(0, max(1, self._total)); bar.setValue(0)
            bar.setTextVisible(False); bar.setFixedSize(86, 6)
            row.addWidget(bar)
            cnt = QLabel("0/0", row_w); cnt.setFixedWidth(40)
            cnt.setStyleSheet(f"color:{TXT_MUT};")
            row.addWidget(cnt)
            row.addStretch(1)
            self._rail_lay.addWidget(row_w)
            self._rail[key] = (dot, nm, bar, cnt)
        self._rail_lay.addStretch(1)
        self._refresh_rail()

    def _refresh_rail(self) -> None:
        rows = _RAIL_REMOTE if self._remote else _RAIL_LOCAL
        for key, label in rows:
            widgets = self._rail.get(key)
            if not widgets:
                continue
            dot, nm, bar, cnt = widgets
            done = self._count(key)
            bar.setRange(0, max(1, self._total)); bar.setValue(done)
            cnt.setText(f"{done}/{self._total}")
            active = key == self._active_stage
            complete = self._total and done >= self._total
            color = CLR_SUCCESS if complete else (WARN if active else
                                                  (ACCENT if done else TXT_MUT))
            dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            nm.setStyleSheet(f"color:{TXT_PRI};" + ("font-weight:700;" if active else ""))

    def _build_legend(self) -> None:
        self._clear(self._legend_lay)
        head = QLabel("Wells", self._legend_host)
        head.setStyleSheet(f"color:{TXT_PRI}; font-weight:600;")
        self._legend_lay.addWidget(head)
        items = [(TXT_MUT, "pending"), (WARN, "processing")]
        items.append((ACCENT, "on server" if self._remote else "processed"))
        items.append((CLR_SUCCESS, "downloaded" if self._remote else "complete"))
        items.append((CLR_DANGER, "failed"))
        for color, txt in items:
            row_w = QWidget(self._legend_host)
            r = QHBoxLayout(row_w); r.setContentsMargins(0, 0, 0, 0); r.setSpacing(7)
            r.addWidget(_dot(color))
            t = QLabel(txt, row_w); t.setStyleSheet(f"color:{TXT_PRI};")
            r.addWidget(t); r.addStretch(1)
            self._legend_lay.addWidget(row_w)
        self._legend_lay.addStretch(1)

    def _well_tooltip(self, well_id: str) -> str:
        w = _canon(well_id)
        if w in self._errored:
            state = "failed"
        elif self._remote and self._has("download", w):
            state = "fetched locally"
        elif self._has("threshold", w):
            state = "thresholded" + ("" if self._remote else " — complete")
        elif self._has("process", w):
            state = "processed"
        elif w in self._active:
            state = "processing"
        elif self._has("upload", w):
            state = "uploaded"
        elif self._has("group", w):
            state = "grouped"
        else:
            state = "pending"
        return f"{w or well_id}: {state}"

    def _on_toggle(self) -> None:
        show = self._toggle.isChecked()
        self._body.setVisible(show)
        self._toggle.setText("▾" if show else "▸")
