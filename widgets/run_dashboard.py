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

# Per-well lifecycle stages, in order. ``error`` is terminal/independent.
_ORDER = {"group": 1, "upload": 2, "active": 3, "process": 4,
          "download": 5, "threshold": 6}
_LABELS = {"group": "grouped", "upload": "uploaded", "active": "processing",
           "process": "processed", "download": "fetched locally",
           "threshold": "complete", "error": "failed"}

# Plate colours by furthest stage (4 readable states + error).
_COL_PROCESSING = WARN          # amber
_COL_FETCHED = ACCENT           # blue — data is local (downloaded / computed)
_COL_COMPLETE = CLR_SUCCESS     # green — thresholded / finished
_COL_ERROR = CLR_DANGER         # red

# Rail rows: (stage-key, label, min furthest-index counted as "done").
_RAIL_LOCAL = [("group", "Group", 1), ("process", "Process", 4),
               ("threshold", "Threshold", 6)]
_RAIL_REMOTE = [("group", "Group", 1), ("upload", "Upload", 2),
                ("process", "Process", 4), ("download", "Download", 5),
                ("threshold", "Threshold", 6)]


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
        self._furthest: dict[str, str] = {}      # well -> furthest stage
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
        self._furthest.clear()
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
        """Advance *well_id* to *stage* (the main driver). Stages only move
        forward; ``error`` always wins."""
        w = _canon(well_id)
        if not w or stage not in _ORDER and stage != "error":
            return
        cur = self._furthest.get(w)
        if cur != "error" and stage != "error":
            if cur is not None and _ORDER.get(stage, 0) <= _ORDER.get(cur, 0):
                return                       # never regress
        self._furthest[w] = stage
        self._plate.setWellColor(w, self._color_for(stage))
        # glyph: gear on the active well, ✗ on failure, otherwise none
        self._plate.setWellMarker(w, "⚙" if stage == "active"
                                  else ("✕" if stage == "error" else None))
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
    def _color_for(self, stage: str) -> str:
        if stage == "error":
            return _COL_ERROR
        if stage == "threshold":
            return _COL_COMPLETE
        if stage == "download":
            return _COL_FETCHED
        if stage == "process":
            # local: data is on disk (blue); remote: computed but not yet
            # fetched — keep amber until the download lands.
            return _COL_PROCESSING if self._remote else _COL_FETCHED
        if stage == "active":
            return _COL_PROCESSING
        return _COL_PROCESSING if stage in ("group", "upload") else _COL_PROCESSING

    def _count_at_least(self, min_idx: int) -> int:
        return sum(1 for s in self._furthest.values()
                   if s != "error" and _ORDER.get(s, 0) >= min_idx)

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
        for key, label, _min in rows:
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
        for key, label, min_idx in rows:
            widgets = self._rail.get(key)
            if not widgets:
                continue
            dot, nm, bar, cnt = widgets
            done = self._count_at_least(min_idx)
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
        items.append((ACCENT, "fetched" if self._remote else "computed"))
        items += [(CLR_SUCCESS, "complete"), (CLR_DANGER, "failed")]
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
        stage = self._furthest.get(w)
        return f"{w or well_id}: {_LABELS.get(stage, 'pending')}"

    def _on_toggle(self) -> None:
        show = self._toggle.isChecked()
        self._body.setVisible(show)
        self._toggle.setText("▾" if show else "▸")
