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
    QFrame, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
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
        self._fov: dict[str, tuple] = {}         # well -> (done, total) FOV-groups
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

        # The single authoritative overall-completion bar for the whole run
        # (grouping → pipeline). Per-stage detail lives in the rail below and
        # per-well detail in the plate; this is the one at-a-glance fraction.
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100); self._bar.setValue(0)
        self._bar.setTextVisible(False)
        root.addWidget(self._bar)

        # body: rail | plate (+ per-well FOV rails beneath it) | legend
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

        # Centre column: the plate map, with the per-well FOV·timepoint
        # progress rails stacked directly beneath it.
        self._center_host = QWidget(self._body)
        center = QVBoxLayout(self._center_host)
        center.setContentsMargins(0, 0, 0, 0); center.setSpacing(6)

        self._plate = WellPlateSelector(self._center_host)
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
        center.addWidget(self._plate)

        # Per-well FOV·timepoint sub-progress: one labelled rail per well
        # currently computing, rebuilt by _refresh_fov. Wrapped in a height-
        # capped scroll area so that when many wells compute at once the rails
        # scroll instead of growing the dashboard and crushing the log below.
        self._fov_host = QWidget()
        self._fov_lay = QVBoxLayout(self._fov_host)
        self._fov_lay.setContentsMargins(0, 0, 0, 0); self._fov_lay.setSpacing(3)
        self._fov_scroll = QScrollArea(self._center_host)
        self._fov_scroll.setWidget(self._fov_host)
        self._fov_scroll.setWidgetResizable(True)
        self._fov_scroll.setFrameShape(QFrame.NoFrame)
        self._fov_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._fov_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # ~6 rails (≈15px each) before scrolling; keeps the log visible.
        self._fov_scroll.setMaximumHeight(96)
        self._fov_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._fov_host.setStyleSheet("background: transparent;")
        center.addWidget(self._fov_scroll)
        center.addStretch(1)
        body.addWidget(self._center_host, 1)

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
        self._fov = {}
        self._active_stage = ""
        self._clear_fov_rows()
        self._fov_scroll.setVisible(False)
        self._bar.setRange(0, 100); self._bar.setValue(0)
        self._plate.clearWellColors()
        self._plate.clearWellMarkers()
        self._plate.setEnabledWells(None)
        self._build_rail(); self._build_legend()
        self._headline.setText("Preparing…")
        self._eta.setText(""); self._status.setText("")

    def set_total(self, total: int) -> None:
        self._total = int(total)
        self._refresh_rail()

    def set_overall_range(self, total: int) -> None:
        """Set the denominator of the overall-completion bar."""
        self._bar.setRange(0, max(1, int(total)))

    def set_overall(self, value: int) -> None:
        """Set the numerator of the overall-completion bar."""
        self._bar.setValue(max(0, int(value)))

    def overall_maximum(self) -> int:
        return self._bar.maximum()

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
        self._refresh_fov()                  # active set changed

    def set_well_fov(self, well_id: str, done: int, total: int) -> None:
        """Per-well FOV·timepoint sub-progress (one group = one FOV × timepoint)
        for the well(s) currently computing."""
        w = _canon(well_id)
        if not w:
            return
        self._fov[w] = (int(done), int(total))
        self._refresh_fov()

    def _clear_fov_rows(self) -> None:
        while self._fov_lay.count():
            it = self._fov_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _refresh_fov(self) -> None:
        """One labelled progress rail per currently-computing well, beneath the
        plate — replaces the old single 'A01 · FOV 7/12' text line."""
        self._clear_fov_rows()
        n_rows = 0
        for w in sorted(self._active):
            if w not in self._fov:
                continue
            done, total = self._fov[w]
            row = QWidget(self._fov_host)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(6)
            lbl = QLabel(f"{w} · FOV", row); lbl.setObjectName("Muted")
            lbl.setFixedWidth(78)
            bar = QProgressBar(row)
            bar.setRange(0, max(1, int(total)))
            bar.setValue(max(0, min(int(done), int(total))))
            bar.setFormat(f"{done}/{total}")
            bar.setTextVisible(True)
            bar.setFixedHeight(12)
            rl.addWidget(lbl)
            rl.addWidget(bar, 1)
            self._fov_lay.addWidget(row)
            n_rows += 1
        # Collapse the scroll area entirely when idle so it reserves no space.
        self._fov_scroll.setVisible(n_rows > 0)

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
