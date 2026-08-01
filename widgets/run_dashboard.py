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

Built on :class:`~widgets.plate_progress.PlateProgress`, which owns the
header/headline/overall-bar/plate+legend chrome shared with
:class:`~datasets.concatenate.dashboard.ConcatDashboard`; this class supplies
only the stage rail and Analyze's remote/local well-colour rules.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

import theme
from widgets.plate_progress import PlateProgress, canon_well, status_dot

# Per-well completed milestones. Stages are tracked INDEPENDENTLY (not as a
# single "furthest" rank) because the order differs local vs remote: on a remote
# run the auto-threshold pass runs on the server *before* the local download, so
# "threshold" must not imply "download" (that made the Download count hit N/N
# before any file was fetched).
_LABELS = {"group": "grouped", "upload": "uploaded", "active": "processing",
           "process": "processed", "download": "fetched locally",
           "threshold": "thresholded", "error": "failed"}
_STAGE_KEYS = ("group", "upload", "active", "process", "threshold", "download")

# Rail rows in the order they actually happen. Remote: the server thresholds
# before the client downloads, so Download is the LAST (terminal, local) step.
_RAIL_LOCAL = [("group", "Group"), ("process", "Process"), ("threshold", "Threshold")]
_RAIL_REMOTE = [("group", "Group"), ("upload", "Upload"), ("process", "Process"),
                ("threshold", "Threshold"), ("download", "Download")]

# Reaching one of these means the well's FOV-groups are all measured, so its
# dial is full. ``group`` and ``upload`` deliberately are not here: a remote
# well reaches both before any of its images have been looked at.
_COMPUTED_STAGES = ("process", "threshold", "download")


class RunDashboard(PlateProgress):
    """At-a-glance per-well run view. Driven entirely by the host."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._remote = False
        self._total = 0
        self._done: dict[str, set] = {}          # stage -> set of wells completed
        self._active: set = set()                # wells currently processing
        self._errored: set = set()
        self._fov: dict[str, tuple] = {}         # well -> (done, total) FOV-groups
        self._active_stage = ""
        self._rail: dict[str, tuple] = {}        # stage -> (dot,name,bar,count)
        super().__init__("Run Progress", parent)
        self.setObjectName("RunDashboard")

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
        self._plate.clearWellFractions()
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
        w = canon_well(well_id)
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
        self._plate.setWellFraction(w, self._well_fraction(w))
        # Only the error mark survives. A computing well used to carry a ⚙,
        # which sat in the middle of what is now its dial — the fill and the
        # colour already say it is working, and the glyph only hid them.
        self._plate.setWellMarker(w, "✕" if w in self._errored else None)
        self._refresh_rail()
        self._refresh_fov()                  # active set changed

    def set_well_fov(self, well_id: str, done: int, total: int) -> None:
        """Per-well FOV·timepoint sub-progress (one group = one FOV × timepoint)
        for the well(s) currently computing."""
        w = canon_well(well_id)
        if not w:
            return
        self._fov[w] = (int(done), int(total))
        self._plate.setWellFraction(w, self._well_fraction(w))
        self._refresh_fov()

    def _well_fraction(self, well: str) -> float | None:
        """How far through this well is, as a wedge fraction for the plate.

        The hue already says which stage the well reached; it cannot say how
        far through that stage it is, so a well on its first FOV-group and one
        on its last looked identical for however long the well took — which on
        a timelapse plate is most of the run. The wedge fills with the
        FOV-group progress the pipeline already reports (``well_fov``), so the
        data costs nothing new.

        Only the stages at or past the compute step can fill the dial. An
        earlier version treated *any* completed stage as done, which on a
        remote run is wrong for every well on the plate: each one reaches
        ``upload`` long before it computes, so every well showed a full dial
        from the moment its zip landed and the feature did nothing precisely
        where the wait is longest. Rendering the dashboard is what caught it —
        the unit tests drove ``active``/``process`` directly and never sent an
        ``upload``, so they all passed.

        ``None`` means no dial at all: the well is pending (or only uploaded),
        or it errored — a partial ring beside the ✕ would suggest it is still
        going. A well that has computed reads full whether or not any FOV
        events arrived for it.
        """
        if well in self._errored:
            return None
        if any(well in self._done.get(stage, ()) for stage in _COMPUTED_STAGES):
            # Full regardless of the last FOV count. A well that finished with
            # some groups failed stops at, say, 3/12, and leaving the dial
            # there would read as still-running forever — the shortfall is
            # reported as a partial well elsewhere, which is where it belongs.
            return 1.0
        done, total = self._fov.get(well, (0, 0))
        if total > 0:
            return max(0.0, min(1.0, done / total))
        return 0.0 if well in self._active else None

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

    # ── internals ───────────────────────────────────────────────────────────
    def _has(self, stage: str, w: str) -> bool:
        return w in self._done.get(stage, ())

    def _well_color(self, w: str) -> str | None:
        """Plate colour for a well, honouring the remote vs local lifecycle.

        Green means "done with the data on your machine": for a remote run that
        is the *download*, not the (server-side) threshold; for a local run it is
        the threshold. Blue is "computed but the data isn't local yet"."""
        if w in self._errored:
            return theme.Colors.danger
        if self._remote:
            if self._has("download", w):
                return theme.Colors.success               # fetched locally — done
            if self._has("process", w) or self._has("threshold", w):
                return theme.Colors.accent                # computed on server, awaiting fetch
            if w in self._active or self._has("upload", w):
                return theme.Colors.warn                   # actively uploading / computing
            if self._has("group", w):
                return theme.Colors.text_muted             # grouped, queued for processing
            return None
        # local
        if self._has("threshold", w):
            return theme.Colors.success
        if self._has("process", w):
            return theme.Colors.accent
        if w in self._active:
            return theme.Colors.warn                       # actively computing
        if self._has("group", w):
            return theme.Colors.text_muted                 # grouped, queued for processing
        return None

    def _count(self, stage: str) -> int:
        return len(self._done.get(stage, ()))

    def _build_rail(self) -> None:
        self._clear(self._rail_lay)
        self._rail = {}
        head = QLabel("Stages", self._rail_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._rail_lay.addWidget(head)
        rows = _RAIL_REMOTE if self._remote else _RAIL_LOCAL
        for key, label in rows:
            row_w = QWidget(self._rail_host)
            row = QHBoxLayout(row_w); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(7)
            dot = status_dot(theme.Colors.text_muted)
            row.addWidget(dot)
            nm = QLabel(label, row_w); nm.setFixedWidth(74)
            nm.setStyleSheet(f"color:{theme.Colors.text_primary};")
            row.addWidget(nm)
            bar = QProgressBar(row_w)
            bar.setRange(0, max(1, self._total)); bar.setValue(0)
            bar.setTextVisible(False); bar.setFixedSize(86, 6)
            row.addWidget(bar)
            cnt = QLabel("0/0", row_w); cnt.setFixedWidth(40)
            cnt.setStyleSheet(f"color:{theme.Colors.text_muted};")
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
            color = (theme.Colors.success if complete else
                     (theme.Colors.warn if active else
                      (theme.Colors.accent if done else theme.Colors.text_muted)))
            dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            nm.setStyleSheet(f"color:{theme.Colors.text_primary};" + ("font-weight:700;" if active else ""))

    def _build_legend(self) -> None:
        self._clear(self._legend_lay)
        head = QLabel("Wells", self._legend_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._legend_lay.addWidget(head)
        items = [(theme.Colors.text_muted, "pending"), (theme.Colors.warn, "processing")]
        items.append((theme.Colors.accent, "on server" if self._remote else "processed"))
        items.append((theme.Colors.success, "downloaded" if self._remote else "complete"))
        items.append((theme.Colors.danger, "failed"))
        for color, txt in items:
            row_w = QWidget(self._legend_host)
            r = QHBoxLayout(row_w); r.setContentsMargins(0, 0, 0, 0); r.setSpacing(7)
            r.addWidget(status_dot(color))
            t = QLabel(txt, row_w); t.setStyleSheet(f"color:{theme.Colors.text_primary};")
            r.addWidget(t); r.addStretch(1)
            self._legend_lay.addWidget(row_w)
        self._legend_lay.addStretch(1)

    def _well_tooltip(self, well_id: str) -> str:
        w = canon_well(well_id)
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
