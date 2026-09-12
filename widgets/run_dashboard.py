"""Visual run dashboard for the Analyze pipeline.

An at-a-glance, per-well view of a pipeline run that sits above the raw log:

* a **stage rail** (left) — every lifecycle stage (Group → Upload → Process →
  Download → Threshold) with a live per-well ``done/total`` count + mini-bar, the
  active stage highlighted. Upload/Download appear only for remote runs;
* a **live plate map** (centre) — an 8×12 :class:`WellPlateSelector` coloured by
  each well's lifecycle stage, each well drawn as a dial filled with its
  progress through whichever stage it is currently inside (bytes while it
  transfers, FOV-groups while it computes), a glyph on failures, and a per-well
  hover tooltip;
* a **legend** (right) explaining the colours.

The widget is a pure view: the host (``AnalyzeTab``) drives it from the same
progress events as the bar, via :meth:`reset`, :meth:`set_well_stage`,
:meth:`set_active_stage`, and the headline/eta/status setters.

Built on :class:`~widgets.plate_progress.PlateProgress`, which owns the
header/headline/overall-bar/plate chrome shared with
:class:`~datasets.concatenate.dashboard.ConcatDashboard` and draws the legend
from the rows a subclass names; this class supplies only the stage rail,
Analyze's remote/local well-colour rules, and the legend rows those rules
imply.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

from ui import theme
from widgets.plate_progress import PlateProgress, canon_well, status_dot

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_BAR_W = 86
_BAR_H = 6
_CNT_W = 40
_NM_W = 74
#: Gap between a stage row's dot, name and bar. Off the 4-px scale on purpose,
#: like the 6 px in ``plate_progress``: these rows are a fixed-width rail whose
#: density decides how many fit, so rounding to a scale step is a design
#: decision rather than a lint fix. Named so the number is stated once.
_STAGE_ROW_SPACING_PX = 7

# Per-well transfer directions, as they arrive on a ``well_xfer`` event.
_XFER_UP = "up"
_XFER_DOWN = "down"

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

# Reaching one of these means the well's FOV-groups are all measured, so as far
# as *computing* goes its dial is full. ``group`` and ``upload`` deliberately
# are not here: a remote well reaches both before any of its images have been
# looked at. Reaching one of these does not settle the dial on its own either —
# a well that has computed still has its output to fetch, and while that fetch
# is in flight the dial shows the fetch.
_COMPUTED_STAGES = ("process", "threshold", "download")


class RunDashboard(PlateProgress):
    """At-a-glance per-well run view. Driven entirely by the host."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._remote = False
        self._total = 0
        # Per-stage overrides for the rail's denominator. A remote run's stages
        # do not all count the same population: Group sees every well on the
        # plate, while Upload/Process/Threshold only ever see the subset the
        # incremental split decided to send. One shared total made every row
        # divide by whichever population happened to set it last, which is how
        # the rail came to read "Group 40/6" and "Download 34/6" — numerators
        # counting the plate against a denominator counting the run.
        self._stage_totals: dict[str, int] = {}
        self._done: dict[str, set] = {}          # stage -> set of wells completed
        self._active: set = set()                # wells currently processing
        self._errored: set = set()
        self._fov: dict[str, tuple] = {}         # well -> (done, total) FOV-groups
        # well -> (direction, fraction) for a transfer in flight right now.
        # Entries are added when a transfer starts and dropped when it reaches
        # 1.0, so membership *is* "this well is moving bytes at the moment".
        self._xfer: dict[str, tuple] = {}
        self._active_stage = ""
        # Plate-wide transfer, as a fraction, while one is in flight — None
        # when there is not one. State rather than a formatted string because
        # two different objects drive this dashboard (analyze.tab handles the
        # runner's own events, analyze.progress the pipeline's), and a label
        # that one of them writes and neither retracts is exactly how it came
        # to sit on "Transferring… 92%" for the whole compute phase.
        self._transfer: float | None = None
        self._rail: dict[str, tuple] = {}        # stage -> (dot,name,bar,count)
        super().__init__("Run Progress", parent)
        self.setObjectName("RunDashboard")

    # ── host-facing API ─────────────────────────────────────────────────────
    def reset(self, *, remote: bool = False, total: int = 0) -> None:
        # Inherited from PlateProgress along with the outcome setters:
        # without this a finished run's terminal state would survive into
        # the next one.
        self.clear_outcome()
        self._remote = remote
        self._total = total
        self._stage_totals = {}
        self._done = {}
        self._active = set()
        self._errored = set()
        self._fov = {}
        self._xfer = {}
        self._active_stage = ""
        self._transfer = None
        self._bar.setRange(0, 100); self._bar.setValue(0)
        self._plate.clearWellColors()
        self._plate.clearWellMarkers()
        self.clear_well_fractions()
        self._plate.setEnabledWells(None)
        self._build_rail(); self._build_legend()
        self._headline.setText("Preparing…")
        self._eta.setText(""); self._status.setText("")

    def set_transfer(self, pct: float | None) -> None:
        """Show (or clear) the plate-wide transfer percentage on the status line.

        ``None`` ends the transfer. Callers do not have to know whether rsync's
        last tick happened to be 100% — whoever observes the run moving on says
        so, and the label goes with it.
        """
        if pct is None:
            if self._transfer is None:
                return
            self._transfer = None
            self.set_status("")
            return
        self._transfer = max(0.0, min(1.0, float(pct)))
        self.set_status(f"Transferring… {int(self._transfer * 100)}%")

    def transfer(self) -> float | None:
        return self._transfer

    def set_total(self, total: int) -> None:
        self._total = int(total)
        self._refresh_rail()

    def set_stage_total(self, stage: str, total: int) -> None:
        """Give one rail row its own denominator, overriding :attr:`_total`.

        For the stages whose population differs from the run's: on a remote run
        Group counts every well the plate holds, while the stages after the
        upload split only ever see the wells actually sent.
        """
        if stage in _STAGE_KEYS:
            self._stage_totals[stage] = int(total)
            self._refresh_rail()

    def _stage_total(self, stage: str) -> int:
        return self._stage_totals.get(stage, self._total)

    def stage_progress(self, stage: str) -> tuple[int, int]:
        """``(done, total)`` for one rail row — the numbers the rail draws.

        For a host that names a stage in the headline as well: the rail is
        collapsible, so the headline is sometimes the only place those counts
        are visible, and reading them from here is what stops the two saying
        different things about the same stage.
        """
        return self._count(stage), self._stage_total(stage)

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
            if stage == "download":
                # The well is fetched, so the frozen download ring has done its
                # job. Both _well_color and _well_fraction already ignore a
                # transfer once this stage is set; dropping it keeps the dict
                # from carrying an entry that can no longer be read.
                self._xfer.pop(w, None)
        self._plate.setWellColor(w, self._well_color(w))
        self.set_well_fraction(w, self._well_fraction(w))
        # Only the error mark survives. A computing well used to carry a ⚙,
        # which sat in the middle of what is now its dial — the fill and the
        # colour already say it is working, and the glyph only hid them.
        self._plate.setWellMarker(w, "✕" if w in self._errored else None)
        self._refresh_rail()

    def set_well_fov(self, well_id: str, done: int, total: int) -> None:
        """Per-well FOV·timepoint sub-progress (one group = one FOV × timepoint)
        for the well(s) currently computing."""
        w = canon_well(well_id)
        if not w:
            return
        self._fov[w] = (int(done), int(total))
        self.set_well_fraction(w, self._well_fraction(w))

    def set_well_transfer(self, well_id: str, direction: str,
                          fraction: float) -> None:
        """Per-well upload (``"up"``) or download (``"down"``) progress, 0..1.

        **Repaints the well as well as filling its dial.** A transfer changes
        which state the well is in, and :meth:`_well_color` says so — a download
        is painted in the colour it is travelling *toward*. Pushing only the
        fraction left that colour computed and discarded, so a downloading well
        kept the blue of the last stage change and filled blue-into-blue: the
        arrival, the one thing the colour exists to show, never happened on
        screen. Every write here goes through both calls for that reason.

        An **upload** reaching 1.0 ends the transfer: the entry is dropped and
        the well goes back to being described by the stages it has reached. A
        well is marked ``upload`` as rsync *starts* sending it, so holding the
        dial full afterwards would claim progress for a well that has not begun
        computing.

        A **download** reaching 1.0 does not end anything, because the well's
        zip is only the first of two transfers — its CSVs follow, and only when
        both have landed is the well fetched. The closing 1.0 is *ignored*: the
        ring freezes at the last fraction actually measured, so the well stays
        green and visibly short of full through the CSV pull, and only
        :meth:`set_well_stage` marking it ``download`` fills it. Two rejected
        alternatives, for the record — dropping the entry returned the well to a
        *full ring in the computed blue*, indistinguishable from one that had
        not started downloading (the reported bug), and storing the 1.0 fills
        the ring green, indistinguishable from one already fetched. A frozen
        near-full ring is the only rendering that is true of both halves.

        The freeze needs a fraction to freeze *at*, so a closing 1.0 with no
        prior tick creates nothing: a well whose size probe failed never had a
        dial, and inventing one at the end would report progress never measured.
        """
        w = canon_well(well_id)
        if not w or direction not in (_XFER_UP, _XFER_DOWN):
            return
        frac = max(0.0, min(1.0, float(fraction)))
        if frac >= 1.0:
            if direction == _XFER_UP:
                self._xfer.pop(w, None)
            # else: a finished download holds its last measured fraction.
        else:
            self._xfer[w] = (direction, frac)
        self._plate.setWellColor(w, self._well_color(w))
        self.set_well_fraction(w, self._well_fraction(w))

    def _well_fraction(self, well: str) -> float | None:
        """How far through its current stage this well is, for the plate's dial.

        The hue says which stage the well is in; it cannot say how far through
        that stage it is, so a well on its first FOV-group and one on its last
        looked identical for however long the well took — which on a timelapse
        plate is most of the run. The dial answers that, and it answers it for
        whichever stage the well is actually inside:

        * a transfer in flight (``well_xfer``) — bytes sent of the well's input
          zip while it uploads, bytes landed of its output zip while it
          downloads. A download outranks everything else the well has done,
          because a well being fetched has by definition already computed;
        * computing (``well_fov``) — FOV-groups measured out of the well's
          total, which the pipeline has always reported;
        * anything else it has reached — full once computed, empty once it has
          entered a stage without finishing any of it.

        An earlier version treated *any* completed stage as done, which on a
        remote run is wrong for every well on the plate: each one reaches
        ``upload`` long before it computes, so every well showed a full dial
        from the moment its zip landed and the feature did nothing precisely
        where the wait is longest. Rendering the dashboard is what caught it —
        the unit tests drove ``active``/``process`` directly and never sent an
        ``upload``, so they all passed.

        ``None`` means no dial at all, and covers exactly two cases: a well
        that has not started (nothing but grouping done, or not even that), and
        one that errored — a partial ring beside the ✕ would suggest it is
        still going.
        """
        if well in self._errored:
            return None
        xfer = self._xfer.get(well)
        if xfer is not None:
            direction, frac = xfer
            # A download in flight describes the well better than any earlier
            # stage does: the compute is over, and the fetch is the wait.
            if direction == _XFER_DOWN and not self._has("download", well):
                return frac
            # An upload only speaks for a well that has not moved past it.
            if (direction == _XFER_UP and well not in self._active
                    and not any(self._has(s, well) for s in _COMPUTED_STAGES)):
                return frac
        if any(well in self._done.get(stage, ()) for stage in _COMPUTED_STAGES):
            # Full regardless of the last FOV count. A well that finished with
            # some groups failed stops at, say, 3/12, and leaving the dial
            # there would read as still-running forever — the shortfall is
            # reported as a partial well elsewhere, which is where it belongs.
            return 1.0
        done, total = self._fov.get(well, (0, 0))
        if total > 0:
            return max(0.0, min(1.0, done / total))
        # Computing, with no group finished yet: an empty dial, not a plain
        # disc. Drawing it as a solid well made the part of the plate that is
        # actively being worked on say nothing about how far in it was.
        if well in self._active:
            return 0.0
        # Uploaded and waiting its turn: a *full* dial. Its archive is on the
        # remote in full, and the dial has just spent the whole upload filling
        # with the bytes sent — ``set_well_transfer`` drops the transfer entry
        # at 1.0, so returning 0.0 here emptied the well at the exact instant
        # the upload completed, which reads as the transfer undoing itself.
        # What the well is waiting for is its turn, not more transfer.
        #
        # A full dial therefore means "uploaded" or "computed" depending on the
        # well's colour, which is the accepted cost of never running backwards.
        if self._has("upload", well):
            return 1.0
        return None

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
            # A transfer in flight is painted in the colour of the state it is
            # travelling TOWARD, not the one the well is leaving. The dial fills
            # from a light tint of its colour to the full one, so painting a
            # download in the "on server" blue it is leaving made the well fill
            # blue-into-blue — light meaning "not yet", against a plate where
            # light is how an unstarted dial looks. It also left no moment at
            # which the well simply *was* blue: it went dark blue, then pale
            # blue, then green. Now it holds blue while it waits on the server
            # and fills green as it lands, so the colour change is the arrival.
            xfer = self._xfer.get(w)
            if xfer is not None and not self._has("download", w):
                direction, _frac = xfer
                if direction == _XFER_DOWN:
                    return theme.Colors.success           # filling toward local
                if direction == _XFER_UP and w not in self._active:
                    return theme.Colors.accent2           # filling toward the remote
            if self._has("download", w):
                return theme.Colors.success               # fetched locally — done
            if self._has("process", w) or self._has("threshold", w):
                return theme.Colors.accent                # computed on server, awaiting fetch
            if w in self._active:
                return theme.Colors.warn                   # computing right now
            if self._has("upload", w):
                # On the remote, waiting its turn. This used to share ``warn``
                # with a computing well, so a plate mid-run was a solid block of
                # amber that said nothing about which wells were actually being
                # worked on — and now that a computing well carries a dial, the
                # two differed in shape while their colour still claimed they
                # were the same state. ``accent2`` is a neighbour of the
                # ``accent`` used for "computed, awaiting fetch": both mean "it
                # is over there", and the pair reads as one family.
                return theme.Colors.accent2
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
            row = QHBoxLayout(row_w); row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(_STAGE_ROW_SPACING_PX)
            dot = status_dot(theme.Colors.text_muted)
            row.addWidget(dot)
            nm = QLabel(label, row_w); nm.setFixedWidth(_NM_W)
            nm.setStyleSheet(f"color:{theme.Colors.text_primary};")
            row.addWidget(nm)
            bar = QProgressBar(row_w)
            bar.setRange(0, max(1, self._total)); bar.setValue(0)
            bar.setTextVisible(False); bar.setFixedSize(_BAR_W, _BAR_H)
            row.addWidget(bar)
            cnt = QLabel("0/0", row_w); cnt.setFixedWidth(_CNT_W)
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
            stage_total = self._stage_total(key)
            bar.setRange(0, max(1, stage_total)); bar.setValue(done)
            cnt.setText(f"{done}/{stage_total}")
            active = key == self._active_stage
            complete = stage_total and done >= stage_total
            color = (theme.Colors.success if complete else
                     (theme.Colors.warn if active else
                      (theme.Colors.accent if done else theme.Colors.text_muted)))
            dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            nm.setStyleSheet(f"color:{theme.Colors.text_primary};" + ("font-weight:700;" if active else ""))

    def _legend_items(self):
        """The only legend here whose rows are decided at run time: a remote
        run gains an Upload stage and renames the two that follow it, since
        "processed" happens on the server and "complete" means fetched back."""
        items = [(theme.Colors.text_muted, "pending")]
        if self._remote:
            items.append((theme.Colors.accent2, "uploaded"))
        items.append((theme.Colors.warn, "processing"))
        items.append((theme.Colors.accent, "on server" if self._remote else "processed"))
        items.append((theme.Colors.success, "downloaded" if self._remote else "complete"))
        items.append((theme.Colors.danger, "failed"))
        return "Wells", items

    def _detail(self, w: str) -> str:
        """The numbers behind a well's dial, for its hover text.

        The dial is a proportion, and a proportion cannot be counted off a
        circle — this is where the counts live now that the per-well progress
        bars beneath the plate are gone.
        """
        xfer = self._xfer.get(w)
        if xfer is not None:
            direction, frac = xfer
            verb = "uploading" if direction == _XFER_UP else "downloading"
            return f" — {verb} {int(round(frac * 100))}%"
        done, total = self._fov.get(w, (0, 0))
        if total > 0 and not any(self._has(s, w) for s in _COMPUTED_STAGES):
            return f" — FOV {done}/{total}"
        return ""

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
        return f"{w or well_id}: {state}{'' if w in self._errored else self._detail(w)}"
