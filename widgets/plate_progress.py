"""Shared chrome for a live per-well run-status dashboard.

Holds what :class:`~widgets.run_dashboard.RunDashboard` (Analyze) and
:class:`~datasets.concatenate.dashboard.ConcatDashboard` (Concatenate) have in
common: a header (title/status/eta/collapse toggle), a headline label, one
overall completion bar, and a 3-column body — a subclass-owned left "rail"
slot, the 8x12 well plate map (each well drawn as a progress dial while it
is being worked on), and a legend. Extracted so the two dashboards' layout/plate/legend wiring
can't drift apart from each other the way it had before this module existed.

The legend is drawn here too, not just hosted here. Every dashboard's legend
is the same picture — a heading over one coloured dot per state, each with its
label — so :meth:`PlateProgress._build_legend` renders it and the subclass says
only *what is in it*, through :meth:`PlateProgress._legend_items`. Five
subclasses had written those eighteen lines out separately before that.

A subclass sets its own instance state, then calls ``super().__init__(title)``;
the base builds every widget above and finishes by invoking the subclass's own
``_build_rail()`` (which must exist by then) and, unless it passed
``show_legend=False``, its ``_legend_items()`` — the same "build state, then
build chrome, then populate" order :class:`RunDashboard` used before this
extraction.
"""

from __future__ import annotations


from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QToolButton, QVBoxLayout, QWidget,
)

from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
from widgets.well_plate_selector import WellPlateSelector
from util.well_token import canonical_well_label
from ui import theme

#: Gap between the plate column and the stage rail beside it. Off the 4-px
#: scale on purpose, like the 6-px root spacing below: this row has to hold a
#: fixed-aspect plate next to a rail whose width is set by its longest stage
#: label, and the nearest scale steps (12 and 16) either crowd the plate or
#: push the rail's text to wrap. Named so the number is stated once.
_BODY_COLUMN_GAP_PX = 14

#: Gap between the plate and the caption stacked under it. Same reasoning: the
#: caption reads as part of the plate at 6 px and as a separate block at 8.
_PLATE_STACK_GAP_PX = 6

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_LEGEND_HOST_W = 190
_RAIL_HOST_W = 238
#: Gap between a legend row's dot and its label. The same 7 px the stage rows
#: use, and off the 4-px scale for the same reason: these are dense fixed-width
#: rows whose legibility, not the scale, decides the number.
_LEGEND_ROW_SPACING_PX = 7


def canon_well(well_id: str) -> str:
    """Normalise a well token to ``A01`` form; '' if it isn't a well.

    The column bound comes from the shared canonicaliser rather than from a
    pattern here, so a dashboard and the plate it colours agree on which
    positions exist — this widget draws an 8x12 grid, and a token outside it
    has nowhere to land.
    """
    return canonical_well_label(well_id) or ""


def status_dot(color: str, d: int = 11) -> QLabel:
    lbl = QLabel()
    lbl.setFixedSize(d, d)
    lbl.setStyleSheet(f"background:{color}; border-radius:{d // 2}px;")
    return lbl


# How a run's terminal state reads: its tone, and the word that stands in when
# the caller has no message. ``done`` shows its summary alone — a summary is
# already a sentence — while the other two prefix theirs, since a bare message
# would not say which state it was.
_OUTCOME_TONES = {
    "done": (theme.Colors.success, "Done"),
    "failed": (theme.Colors.danger, "Failed"),
    "cancelled": (theme.Colors.warn, "Cancelled"),
}


def _without_state_prefix(message: str, word: str) -> str:
    """Drop a leading ``"<word> — "`` from *message*, so it isn't said twice.

    Every Datasets worker announces a stopped run as ``"Cancelled — <why>"``
    (`datasets/job_queue/controller.py` matches on that prefix), and the caller
    then hands the whole string to a setter that prefixes it again. A separator
    is required, not merely the word: a real failure often *begins* with its
    state — "Failed to open …" is a sentence, not a prefix, and must survive
    whole.
    """
    text = str(message or "").strip()
    if text[: len(word)].lower() != word.lower():
        return text
    rest = text[len(word):].lstrip()
    if not rest:
        return ""
    if rest[0] not in "—–-:":
        return text
    return rest[1:].lstrip()


def apply_outcome(label, outcome: str, message: str = "", *,
                  tooltip: str | None = None) -> None:
    """Paint *label* with a run's terminal state — one wording, one tone.

    The three states were painted by hand on every Datasets dashboard before
    :class:`PlateProgress` gathered them; this is the same decision hoisted one
    step further out, because a page can report an outcome without owning a
    dashboard at all (Convert OME-TIFF, whose status strip is a bare progress
    bar over the acquisition plate it already draws). Module-level for the same
    reason :func:`fraction_or_none` is: two kinds of host need it, so it gets
    one home rather than a second copy to drift.

    The full text stays reachable as a tooltip even when the caller shortens
    what it displays — a pipeline refusal names figures and remedies the
    headline has no room for. A **cancel** is the exception: it carries no
    explanation, so it must never keep a failure's.
    """
    colour, fallback = _OUTCOME_TONES[outcome]
    body = _without_state_prefix(message, fallback)
    if outcome == "done":
        label.setText(body or fallback)
    else:
        label.setText(f"{fallback} — {body}" if body else fallback)
    if outcome == "cancelled":
        label.setToolTip("")
    else:
        label.setToolTip(tooltip if tooltip is not None else (message or fallback))
    label.setStyleSheet(f"color:{colour};")


def fraction_or_none(done: int, total: int) -> float | None:
    """``done / total`` clamped to 0.0–1.0, or ``None`` when *total* is not
    positive.

    A caller that has not counted the work yet is reporting "unknown", and an
    unknown amount of progress must not render as a definite zero-filled dial
    — that is the same picture as a well that has genuinely done nothing.

    Module-level rather than a method because both kinds of dial host need it:
    :meth:`PlateProgress.set_well_progress`, and a controller that paints its
    page's *own* plate instead of a dashboard's (``datasets.subset.controller``
    — see :meth:`PlateProgress.set_well_fraction` for why it has no dashboard
    plate to reach). One rule, one home, no second copy to drift.
    """
    if int(total) <= 0:
        return None
    return max(0.0, min(1.0, max(0, int(done)) / int(total)))


class PlateProgress(QWidget):
    """Header/headline/overall-bar/plate+legend chrome, shared by RunDashboard
    and ConcatDashboard. A subclass builds its own left-slot content (a stage
    or phase rail) into ``self._rail_host``/``self._rail_lay`` via its own
    ``_build_rail``/``_refresh_rail``/``_build_legend``, and provides
    ``_well_tooltip`` for the plate's hover text."""

    def __init__(
        self, title: str, parent: QWidget | None = None, *,
        show_plate: bool = True, show_legend: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PlateProgress")
        # Which terminal state the run reached, or "" while it is still running
        # (or before it started). Subclass rails read it to decide whether to
        # draw a live count or a finished one.
        self._outcome = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(theme.Spacing.md, theme.Spacing.sm,
                                theme.Spacing.md, theme.Spacing.sm)
        # 6 px sits between ``Spacing.xs`` and ``Spacing.sm`` and matches no
        # token. Left as a literal rather than rounded: this rail packs a
        # header, a plate and a group list into a fixed-width column, so either
        # scale step changes how much of the list fits, and that is a design
        # decision rather than a lint fix.
        root.setSpacing(6)

        # header: title + status + collapse toggle
        hdr = QHBoxLayout(); hdr.setSpacing(theme.Spacing.sm)
        self._title_label = QLabel(title, self)
        hdr.addWidget(self._title_label)
        self._status = QLabel("", self); self._status.setObjectName("Muted")
        hdr.addWidget(self._status)
        self._header = hdr                       # `add_header_widget` inserts beside the status
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
        # A real failure message (e.g. naming every colliding well token) is
        # long enough to force a horizontal scrollbar on the whole page and
        # push everything above the dashboard off-screen without this — the
        # one label in this widget with no upper bound on its content length.
        self._headline.setWordWrap(True)
        root.addWidget(self._headline)

        # The single authoritative overall-completion bar for the whole run.
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100); self._bar.setValue(0)
        self._bar.setTextVisible(False)
        root.addWidget(self._bar)

        # body: rail | plate | legend
        self._body = QWidget(self)
        body = QHBoxLayout(self._body)
        body.setContentsMargins(0, theme.Spacing.xs, 0, 0)
        body.setSpacing(_BODY_COLUMN_GAP_PX)

        self._rail_host = QWidget(self._body)
        self._rail_lay = QVBoxLayout(self._rail_host)
        self._rail_lay.setContentsMargins(0, 0, 0, 0)
        self._rail_lay.setSpacing(theme.Spacing.sm)
        self._rail_host.setFixedWidth(_RAIL_HOST_W)
        body.addWidget(self._rail_host)

        if show_plate or show_legend:
            d1 = QFrame(self._body); d1.setFrameShape(QFrame.VLine); d1.setObjectName("VRule")
            body.addWidget(d1)

        # A subclass whose page already has its own interactive plate map
        # for input (e.g. Subset's well picker) passes show_plate=False —
        # driving progress onto that widget directly instead of a second,
        # redundant status plate here. Skipping the whole centre column
        # (not just hiding self._plate) avoids leaving a wide dead gap
        # between the rail and the legend, the same class of empty-space
        # bug a QSizePolicy/maximumWidth mismatch caused on self._plate
        # itself elsewhere in this file.
        if show_plate:
            self._center_host = QWidget(self._body)
            center = QVBoxLayout(self._center_host)
            center.setContentsMargins(0, 0, 0, 0)
            center.setSpacing(_PLATE_STACK_GAP_PX)

            self._plate = WellPlateSelector(self._center_host)
            self._plate.setDragSelectEnabled(False)
            self._plate.setRowColumnSelectable(False)
            # Paint coloured wells in the raised/lit style (not the dim recessed
            # tint) so run states read clearly on the status map.
            self._plate.setColoredWellsLit(True)
            # Deliberately NOT WA_TransparentForMouseEvents. That attribute
            # makes the whole subtree invisible to hit-testing, which takes the
            # hover tooltip with it — and the per-well detail a dashboard
            # reports (``FOV 7/12``, ``downloading 42%``) exists nowhere else in
            # the app, so setting it removed the only place those numbers are
            # readable. Read-only-ness comes from ``role="status"`` below, which
            # puts the plate in "passive" selection mode: a click emits
            # ``wellActivated`` and changes nothing, and no dashboard connects
            # that signal.
            # The app-wide plate geometry (widgets/plate_geometry.py): the
            # "status" role hides the built-in action row and makes the plate
            # read-only, and the caps give it the same cell scale as every other
            # plate map in the app. Centred, like all of them — the centre column
            # is as wide as the page allows, so a plate pinned to one edge of it
            # reads as misaligned against the picker above.
            apply_plate_geometry(self._plate, role="status")
            self._plate.setWellTooltipProvider(self._well_tooltip)
            center.addWidget(self._plate, 0, PLATE_ALIGNMENT)

            center.addStretch(1)
            body.addWidget(self._center_host, 1)

            if show_legend:
                d2 = QFrame(self._body); d2.setFrameShape(QFrame.VLine); d2.setObjectName("VRule")
                body.addWidget(d2)

        if show_legend:
            self._legend_host = QWidget(self._body)
            self._legend_lay = QVBoxLayout(self._legend_host)
            self._legend_lay.setContentsMargins(0, 0, 0, 0)
            self._legend_lay.setSpacing(theme.Spacing.sm)
            # Wide enough for the longest legend string in current use
            # ("merged — no CSV on one side") without wrapping; subclasses'
            # legend labels also carry setWordWrap(True) as a second line of
            # defence, so a future longer label wraps to two lines instead of
            # being silently clipped the way a fixed-but-too-narrow column did.
            self._legend_host.setFixedWidth(_LEGEND_HOST_W)
            body.addWidget(self._legend_host)
        if not show_plate:
            # With the centre column skipped there is no widget carrying a
            # stretch factor left in this layout — every remaining item
            # (rail_host, any divider, legend_host) is fixed-width, so with
            # nowhere explicit to put the page's leftover width Qt
            # distributed it as blank padding around the fixed items instead,
            # reading as a wide dead gap. A trailing stretch collects that
            # leftover at the row's own right edge instead, keeping
            # rail/divider/legend packed together at the left the way the
            # column layout intends.
            body.addStretch(1)

        root.addWidget(self._body)
        self._build_rail()
        if show_legend:
            self._build_legend()

    # ── shared host-facing API ──────────────────────────────────────────────
    # ── terminal states ─────────────────────────────────────────────────────
    # Every dashboard ends a run in one of three ways and painted the headline
    # itself to say so, in byte-identical copies. The wording, the tone and the
    # tooltip rule are the same decision for all of them, so they live here; a
    # dashboard with something extra to do on a state change overrides
    # :meth:`_on_outcome_changed` rather than the setter, which is what keeps
    # ``tests/test_datasets_dashboard_outcomes.py``'s no-override rule true.

    def clear_outcome(self) -> None:
        """Forget the previous run's terminal state. Call from ``reset``.

        The headline's tooltip and colour go with it. Both are set by the
        outcome setters and neither belongs to the run that follows: a tooltip
        nobody clears outlives the run it describes, so hovering a *successful*
        plate produces the previous run's failure text, and a colour nobody
        clears paints the next run's live progress and its "Complete —" line in
        danger red. Both are "good output indistinguishable from degraded",
        which is the thing these outcome states exist to prevent.

        This is not the callers' job. Every Datasets tool happened to be immune
        because each calls ``set_done`` on success, whose green overwrites the
        red; a host that only ever reports failures (Analyze) had nothing to
        correct it with.
        """
        self._outcome = ""
        self._clear_headline_decoration()

    def _clear_headline_decoration(self) -> None:
        """Drop the tooltip and colour an outcome setter left on the headline."""
        self._headline.setToolTip("")
        self._headline.setStyleSheet("")

    def set_done(self, summary: str, *, tooltip: str | None = None) -> None:
        self._outcome = "done"
        apply_outcome(self._headline, "done", summary, tooltip=tooltip)
        self._on_outcome_changed()

    def set_failed(self, message: str, *, tooltip: str | None = None) -> None:
        self._outcome = "failed"
        apply_outcome(self._headline, "failed", message, tooltip=tooltip)
        self._on_outcome_changed()

    def set_cancelled(self, message: str = "") -> None:
        self._outcome = "cancelled"
        apply_outcome(self._headline, "cancelled", message)
        self._on_outcome_changed()

    def _on_outcome_changed(self) -> None:
        """Hook for a dashboard whose rail shows the outcome. Default no-op."""

    def set_headline(self, text: str) -> None:
        # Live progress text, so it also leaves the terminal state's decoration
        # behind — see ``clear_outcome``.
        self._headline.setText(text or "")
        self._clear_headline_decoration()

    def outcome(self) -> str:
        """The terminal state this run reached — ``""`` while it is still going.

        ``"done"`` / ``"failed"`` / ``"cancelled"``, as the outcome setters
        recorded it. Readable from outside so a host that *derives* the headline
        can stand down once the run has ended: the reason a run failed must
        outlive the phase it failed in.
        """
        return self._outcome

    def headline_text(self) -> str:
        """What the headline currently says.

        For a caller that derives the headline and repaints it only when the
        sentence changes: comparing against the label itself, rather than
        against its own copy of what it last wrote, keeps the comparison honest
        when somebody else (a run's terminal state) has written it since.
        """
        return self._headline.text()

    def set_eta(self, text: str) -> None:
        self._eta.setText(text or "")

    def set_status(self, text: str) -> None:
        self._status.setText(text or "")

    def set_overall_range(self, total: int) -> None:
        """Set the denominator of the overall-completion bar."""
        self._bar.setRange(0, max(1, int(total)))

    def set_overall(self, value: int) -> None:
        """Set the numerator of the overall-completion bar."""
        self._bar.setValue(max(0, int(value)))

    def overall_maximum(self) -> int:
        return self._bar.maximum()

    # ── per-well progress dials ─────────────────────────────────────────────
    # A well's colour says which stage it reached; it cannot say how far through
    # that stage it is, so a well on its first FOV-group and one on its last look
    # identical for however long the well takes. These three promote
    # ``WellPlateSelector``'s own fraction API onto the dashboard so every tool
    # can draw that progress as a dial on the well itself, and so no subclass has
    # to reach through ``self._plate`` — which does not exist at all when the
    # dashboard is built with ``show_plate=False`` (SubsetDashboard paints run
    # progress onto its page's own picker instead). All three no-op in that case
    # rather than raising.
    #
    # A no-op is the right base-class behaviour, but it is not a progress
    # report: a controller whose dashboard has no plate must paint the plate it
    # does own rather than call these and assume something happened. Subset is
    # that case, and it drives its picker plate's ``setWellFraction`` directly
    # through the shared ``fraction_or_none`` above.

    def set_well_fraction(self, well_id: str, fraction: float | None) -> None:
        """Fill *well_id*'s dial to *fraction* (0.0–1.0), or drop it with ``None``."""
        plate = getattr(self, "_plate", None)
        if plate is None:
            return
        well = canon_well(well_id)
        if not well:
            return
        if fraction is None:
            plate.setWellFraction(well, None)
            return
        plate.setWellFraction(well, max(0.0, min(1.0, float(fraction))))

    def set_well_progress(self, well_id: str, done: int, total: int) -> None:
        """Fill *well_id*'s dial to ``done / total``, per :func:`fraction_or_none`
        — a non-positive *total* clears the dial instead of dividing by it."""
        self.set_well_fraction(well_id, fraction_or_none(done, total))

    def clear_well_fractions(self) -> None:
        """Drop every dial, leaving the wells' own state colours untouched."""
        plate = getattr(self, "_plate", None)
        if plate is not None:
            plate.clearWellFractions()

    # ── internals shared by subclasses ──────────────────────────────────────
    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)        # un-render immediately
                w.deleteLater()

    def add_header_widget(self, widget: QWidget) -> None:
        """Show *widget* in the header, after the run status and before the ETA.

        For a host's own run-state glyph — Analyze's Slurm-job icon — so it sits with the status it qualifies
        rather than in a form the user is no longer looking at while the run goes.
        """
        self._header.insertWidget(self._header.indexOf(self._status) + 1, widget)

    def _build_legend(self) -> None:
        """Draw the legend: a heading, then one dot-and-label row per item.

        Every dashboard's legend is the same picture — which colour means
        which state — and differs only in the heading and the list. So the
        subclass supplies the list through :meth:`_legend_items` and this
        draws it, rather than each subclass writing the same eighteen lines
        with its own words in the middle.

        It clears first because it is called again on every ``reset``: a run
        that redraws its legend must replace the rows, not stack a second set
        under them.
        """
        title, items = self._legend_items()
        self._clear(self._legend_lay)
        head = QLabel(title, self._legend_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._legend_lay.addWidget(head)
        for color, text in items:
            row = QWidget(self._legend_host)
            r = QHBoxLayout(row)
            r.setContentsMargins(0, 0, 0, 0)
            r.setSpacing(_LEGEND_ROW_SPACING_PX)
            r.addWidget(status_dot(color))
            label = QLabel(text, row)
            label.setStyleSheet(f"color:{theme.Colors.text_primary};")
            r.addWidget(label)
            r.addStretch(1)
            self._legend_lay.addWidget(row)
        self._legend_lay.addStretch(1)

    def _on_toggle(self) -> None:
        show = self._toggle.isChecked()
        self._body.setVisible(show)
        self._toggle.setText("▾" if show else "▸")

    # ── hooks a subclass must implement ─────────────────────────────────────
    def _build_rail(self) -> None:
        raise NotImplementedError

    def _legend_items(self) -> tuple[str, list[tuple[str, str]]]:
        """The legend's heading and its ``(colour, label)`` rows.

        Still a hook that raises rather than a default of no rows: a subclass
        that asks for a legend and forgets to say what is in it should fail at
        construction, not draw an empty column. One that wants no legend passes
        ``show_legend=False`` instead, and is never asked.
        """
        raise NotImplementedError

    def _well_tooltip(self, well_id: str) -> str:
        raise NotImplementedError
