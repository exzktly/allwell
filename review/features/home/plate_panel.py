"""Home's plate map: what the last run actually produced, well by well.

Built on :class:`~widgets.well_plate_selector.WellPlateSelector` directly rather
than on :class:`~widgets.plate_progress.PlateProgress`. That base is a *run
dashboard* — headline, overall progress bar, ETA, done/failed/cancelled outcome
setters — and it gives its plate the ``status`` geometry role, which is passive
selection *and* report-only (no corner select-all, no pointing-hand cursor).
Home's plate is neither: it has no run to report on, and clicking a well selects
it. Undoing a read-only base's read-only-ness would be fighting it, so this
takes the ``editor`` role and borrows only the two lines it wants — the lit
colouring and the shared geometry call.

**Every colour here means what it means on the other plate maps in the app**,
because a reader who has learnt one plate has learnt all of them:

===================== ============== ==========================================
State                 Token          The same token elsewhere
===================== ============== ==========================================
complete              ``success``    done / merged / complete on every dashboard
partial               ``warn``       Validate's *partial*, and work in flight
failures recorded     ``danger``     Validate's *missing*, any errored well
images, no            ``accent``     Concatenate's *merged — no measurements CSV
measurements                         on one side*: the identical condition, and
                                     the convention that ``accent`` flags the
                                     anomalous well rather than a normal one
staged, never         ``text_muted`` *pending* on Concatenate, Merge Plates and
processed                            Import Loose; *no output yet* on Validate
===================== ============== ==========================================

``accent2`` is deliberately unused: it means "belongs to the other of two normal
groups" on Merge Plates, and no state here is that.

**The two states that describe an absence are painted in no colour at all.**
``unchecked`` (there is no completeness record) and ``no_output`` (the record
has the well, and it has no evidence at all) both assert nothing about a well,
so they get the plate's own uncoloured cell rather than a hue that would. They
cannot be confused with each other: ``unchecked`` is what a plate with no record
is made of and ``no_output`` can only come from a record, so only one of them is
ever on screen.

*Partial* is the one state given a shape as well as a hue — it is the state this
page exists to make visible, and an arc survives a small cell, colour blindness
and a fast skim.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ui import theme
from widgets.plate_geometry import apply_plate_geometry
from widgets.well_plate_selector import WellPlateSelector

from .collect import (
    CELL_COMPLETE,
    CELL_FAILED,
    CELL_INPUT_ONLY,
    CELL_NO_MEASUREMENTS,
    CELL_NO_OUTPUT,
    CELL_CSV_ONLY,
    CELL_PARTIAL,
    CELL_UNCHECKED,
)

#: One colour per painted state. Read through ``theme.Colors`` so a theme swap
#: carries the plate with it, and drawn from the status palette every other
#: plate map uses (see the table above) rather than from the well-identity
#: palette the plots key to wells.
STATE_COLORS = {
    CELL_COMPLETE: theme.Colors.success,
    CELL_PARTIAL: theme.Colors.warn,
    CELL_FAILED: theme.Colors.danger,
    CELL_NO_MEASUREMENTS: theme.Colors.accent,
    # Numbers whole, output archive gone. Its own colour, because painting it
    # like `partial` would say the run has work left to do and painting it
    # neutral would say nothing was checked — and the first version of this
    # change did the second, so such a well was indistinguishable from one
    # nobody had looked at. Not `accent2`, whose own comment reserves it for
    # "the other of two normal groups" and forbids anomaly use, and not
    # `threshold`, which the plate's render guard proved indistinguishable
    # from `warn` once painted.
    CELL_CSV_ONLY: theme.Colors.stale,
    CELL_INPUT_ONLY: theme.Colors.text_muted,
}

#: The states that assert nothing, and so are painted in nothing: the plate's
#: own uncoloured cell. Every other state carries a colour, and no state carries
#: a colour another state also carries — a plate on which two states looked
#: alike would be a plate that cannot be read against its legend.
NEUTRAL_STATES = (CELL_UNCHECKED, CELL_NO_OUTPUT)

STATE_LABELS = {
    CELL_COMPLETE: "Complete",
    CELL_PARTIAL: "Partial",
    CELL_FAILED: "Failures recorded",
    CELL_INPUT_ONLY: "Staged, never processed",
    CELL_NO_MEASUREMENTS: "Images, no measurements",
    CELL_CSV_ONLY: "Measurements, no output archive",
    CELL_UNCHECKED: "Not checked",
    CELL_NO_OUTPUT: "No output yet",
}


def build_home_plate(parent: QWidget) -> WellPlateSelector:
    """A selectable plate sized like every other in the app.

    ``role="editor"``: the wells are clickable, which is what separates this
    from a dashboard's read-only map, but the built-in All / Invert / Clear row
    is not shown. Those three act on the *plot selection*, and nothing on this
    page plots — selecting all 96 positions of a plate that holds four wells
    changes a number in a status bar and nothing else on screen. A control whose
    effect is invisible from where it sits is worse than no control.
    """
    # No objectName: the plate paints every well itself through QPainter, so a
    # style hook here would resolve to nothing. The page reaches it through the
    # widget registry (``_home_plate``), not through a selector.
    plate = WellPlateSelector(parent)
    plate.setColoredWellsLit(True)
    apply_plate_geometry(plate, role="editor")
    return plate


def paint_plate(plate: WellPlateSelector, cells: dict, fractions: dict) -> None:
    """Paint one state per well, plus a coverage arc for the part-done ones.

    A well the record does not mention — and one whose state asserts nothing —
    is left neutral rather than being given a colour that would speak for it.
    """
    plate.clearWellColors()
    plate.clearWellFractions()
    for well, state in (cells or {}).items():
        colour = STATE_COLORS.get(state)
        if colour is None:
            # Neutral, not skipped: an earlier paint may have left a colour on
            # this well, and ``clearWellColors`` above only covers the ones the
            # plate still holds.
            plate.setWellState(well, "neutral")
            continue
        plate.setWellState(well, f"color:{colour}")
    for well, fraction in (fractions or {}).items():
        plate.setWellFraction(well, fraction)


#: The glyph a well carries when the run flagged one or more of its fields on
#: image quality.
#:
#: A **marker**, not a ninth colour, and that is a constraint rather than a
#: preference. ``STATE_COLORS`` above is closed under a written invariant —
#: no state carries a colour another state also carries — and the two
#: candidates a ninth state would reach for are both spoken for: ``accent2``
#: is reserved for "the other of two normal groups" and forbidden for anomaly
#: use, and ``threshold`` was *proven* indistinguishable from ``warn`` once
#: painted, by this plate's own render guard.
#:
#: It is also the right channel on its own terms. Field quality is orthogonal
#: to coverage: a well can be complete and out of focus, or partial and
#: perfectly exposed, and those are two facts about one well rather than two
#: values of one fact. A colour can only say one thing at a time.
FLAGGED_FIELD_GLYPH = "!"


def legend_marker_style() -> str:
    """The legend key for the marker: the glyph inside an outlined box.

    A bare glyph beside the colour rows was measured at about 2x9 px of ink
    against a 12x12 solid swatch — roughly 15% of the area — so the last row
    looked as if it had no key at all. The plate's own neutral state already
    establishes the vocabulary for a key that is not a solid fill (an outlined
    box, ``legend_swatch_style`` for a state the plate leaves uncoloured), so
    this borrows it rather than inventing a third shape. The glyph goes
    inside, bold, which keeps the row's visual mass while still reading as a
    different channel from the fills above it.
    """
    return (f"background:transparent; "
            f"border:1px solid {theme.Colors.border_strong}; "
            f"border-radius:{theme.Radii.xs}px; "
            f"font-weight:700; color:{theme.Colors.text_primary};")


def paint_field_quality(plate: WellPlateSelector, flagged_wells) -> None:
    """Mark the wells holding at least one flagged field. Clears first.

    Clearing is not housekeeping: this runs again on every repaint, and a
    marker left from the previous dataset — or from before the user changed
    something — would be an assertion about a well nobody had measured.
    """
    plate.clearWellMarkers()
    for well in sorted(flagged_wells or ()):
        plate.setWellMarker(well, FLAGGED_FIELD_GLYPH)


def well_tooltip(well: str, cells: dict, record_wells: dict,
                 field_quality: dict | None = None) -> str:
    """What a well says when the pointer rests on it.

    Reads the numbers the record already holds rather than deriving anything —
    the tooltip and the colour must never be able to disagree.

    *field_quality* maps well → the sentence describing what was flagged
    there, built once by the caller from the same verdicts that decided the
    marker. A glyph with no explanation is a puzzle rather than a finding, and
    deriving the sentence here would be a second derivation of something the
    marker already asserts.
    """
    state = (cells or {}).get(well)
    if state is None:
        return f"{well} · not in this dataset"
    label = STATE_LABELS.get(state, state)
    quality = (field_quality or {}).get(well, "")
    rec = (record_wells or {}).get(well)
    if not isinstance(rec, dict):
        base = f"{well} · {label.lower()}"
        return f"{base} · {quality}" if quality else base
    intent = int(rec.get("intent") or 0)
    covered = int(rec.get("covered_any") or 0)
    bits = [f"{well} · {label.lower()}"]
    if intent:
        bits.append(f"{covered} of {intent} field group(s)")
    if int(rec.get("covered_csv") or 0) != covered:
        bits.append(f"{int(rec.get('covered_csv') or 0)} in the measurements CSV")
    if rec.get("failed"):
        bits.append(f"{len(rec['failed'])} failed group(s)")
    if quality:
        bits.append(quality)
    return " · ".join(bits)
