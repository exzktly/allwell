"""Qt-free planning + validation for the OME-TIFF converter.

Reads the OME layout (via :mod:`pipeline.ome_explode`) and resolves the user's
well-grid, FOVs-per-well and frame-order into a :class:`ConversionPlan` — the
same split maths the explode uses, surfaced ahead of the run so the page can show
a live readout and block an inconsistent request before any file is written.

No Qt, no file writing — so it is unit-testable without an event loop, and the
page/worker layer stays a thin shell on top (CLAUDE.md §3/§5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from datasets.core import convert_marker as marker
from datasets.core.convert_marker import ResumeState  # noqa: F401 (re-export)

from pipeline.ome_explode import (
    FRAME_ORDERS,
    OmeLayout,
    read_ome_layout,
)
from datasets.ome_import.stage_layout import (
    mirrored,
    single_cycle,
    infer_frame_order,
    infer_stage_layout,
    path_in_cells,
    place,
)
from util.plate_grid import (
    block_well_labels,
    normalize_well_order,
    well_row_col,
)

# The channel-role heuristic moved to the shared ``util.channel_roles`` so the
# Analyze page can reuse it; re-exported here so the converter's imports and its
# tests keep resolving through this module.
from util.well_token import canonical_well_label
from util.channel_roles import (  # noqa: F401 (re-export)
    CHANNEL_ROLES,
    ChannelTokens,
    channel_tokens_from_roles,
    default_channel_roles,
    guess_channel_role,
)

_OME_SUFFIXES = (".ome.tif", ".ome.tiff")


class OmeImportError(Exception):
    """A user-facing reason the OME-TIFF can't be converted with these settings."""


@dataclass(frozen=True)
class ConversionParams:
    """The user's de-interleave settings from the converter page."""

    grid_rows: int
    grid_cols: int
    origin: str = "A01"
    direction: str = "row"
    # False rasters the grid: every line swept the same way, the stage flying
    # back between them. A raster read as a snake mislabels every second line
    # backwards, with the well count still correct — so it is an input, not an
    # assumption.
    serpentine: bool = True
    fovs_per_well: int | None = None      # None = derive
    frame_order: str = "single"           # single-image files only
    experiment: str = ""                  # blank = derive from the filename
    workers: int = 0                      # 0 = auto (per-well thread pool)
    # An explicit acquisition ordering (click-defined on the plate mockup). When
    # non-empty it replaces the rectangular serpentine: wells are visited in this
    # order and the grid fields only seed it. May be non-rectangular.
    well_order: tuple[str, ...] = ()


@dataclass
class ConversionPlan:
    """The resolved shape of a conversion, for the readout and a sanity gate."""

    single_image: bool
    n_positions: int
    n_timepoints: int
    fovs_per_well: int
    well_labels: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    # Which of the two well-ordering inputs produced ``well_labels``. The two
    # are not interchangeable to a reader of the summary: the same counts can
    # come from the grid fields or from a path clicked on the plate, and only
    # one of those is what the user last touched.
    hand_drawn: bool = False

    @property
    def n_wells(self) -> int:
        return len(self.well_labels)

    def summary(self) -> str:
        """A one-line description of what a run would produce.

        Names the wells at both ends of the acquisition order, not only how many
        there are: a count alone is the same sentence whether the order starts
        where the user meant it to or one well earlier, which is exactly the
        mistake an ordering this page can get wrong produces.
        """
        chans = ", ".join(self.channels) if self.channels else "?"
        span = ""
        if self.well_labels:
            first, last = self.well_labels[0], self.well_labels[-1]
            span = f" {first}" if first == last else f" {first}→{last}"
        # "explicit path", not "clicked": the same order can come from the
        # user's clicks or from the file's own coordinates, and calling the
        # second one clicked describes something that did not happen.
        source = " (explicit path)" if self.hand_drawn else " (from the grid)"
        return (
            f"{self.n_wells} well(s){span}{source} × {self.fovs_per_well} FOV(s) × "
            f"{self.n_timepoints} timepoint(s) · channels: {chans}"
        )


def parse_count(text: object) -> tuple[int | None, bool]:
    """Read a whole-number field as ``(value, well_formed)``.

    Blank is ``(None, True)`` — every count on this page has a legitimate blank
    meaning ("auto", or "no grid entered"). Anything that is not a whole number
    is ``(None, False)``, which is the distinction the page had no way to make
    while it parsed with a bare ``int()``-or-zero: ``"two"``, ``"1.5"`` and
    ``"0"`` all became the same silent "auto" as an empty box, so a typo and a
    deliberate choice looked identical on screen.
    """
    raw = str(text if text is not None else "").strip()
    if not raw:
        return None, True
    try:
        return int(raw), True
    except (TypeError, ValueError):
        return None, False


@dataclass(frozen=True)
class RawFields:
    """The converter page's inputs exactly as typed, before any coercion.

    The page hands these over verbatim and this module decides what they mean,
    so the parsing rules and their wording are unit-testable without an event
    loop and the view stays a builder (CLAUDE.md §3). ``well_order`` is the
    acquisition path only when the user owns it — a live grid *preview* on the
    plate is a picture of the grid fields, not a second input, and feeding it
    back in is how a clicked well could inherit the grid's seed well.
    """

    rows: str = ""
    cols: str = ""
    fovs: str = ""
    origin: str = ""
    workers: str = ""
    direction: str = "row"
    serpentine: bool = True
    frame_order: str = "single"
    experiment: str = ""
    well_order: tuple[str, ...] = ()

    @property
    def hand_drawn(self) -> bool:
        """True when the wells come from a clicked path rather than the grid."""
        return bool(self.well_order)

    def problems(self) -> list[str]:
        """User-facing reasons these fields can't be read as they were typed.

        Only malformed *values* — a grid that doesn't fit the file is the plan's
        job, not this one's. Empty when every box parses.
        """
        out: list[str] = []
        if not self.hand_drawn:
            for text, name in ((self.rows, "Rows"), (self.cols, "Columns")):
                value, ok = parse_count(text)
                if not ok:
                    out.append(f"{name} must be a whole number.")
                elif value is not None and value <= 0:
                    out.append(f"{name} must be 1 or more.")
        fovs, ok = parse_count(self.fovs)
        if not ok:
            out.append("FOVs per well must be a whole number, or blank for auto.")
        elif fovs is not None and fovs <= 0:
            out.append("FOVs per well must be 1 or more, or blank for auto.")
        workers, ok = parse_count(self.workers)
        if not ok:
            out.append("Workers must be a whole number, or blank for auto.")
        elif workers is not None and workers <= 0:
            out.append("Workers must be 1 or more, or blank for auto.")
        return out

    def to_params(self) -> ConversionParams:
        """The typed settings these fields describe.

        Malformed values fall back to the same neutral defaults the page has
        always used, which is safe *because* :meth:`problems` reports them
        first and the page refuses to run while it returns anything.
        """
        rows, _ = parse_count(self.rows)
        cols, _ = parse_count(self.cols)
        fovs, _ = parse_count(self.fovs)
        workers, _ = parse_count(self.workers)
        return ConversionParams(
            grid_rows=max(0, rows or 0),
            grid_cols=max(0, cols or 0),
            origin=self.origin.strip() or "A01",
            direction=self.direction or "row",
            serpentine=self.serpentine,
            fovs_per_well=fovs if (fovs or 0) > 0 else None,
            frame_order=self.frame_order or "single",
            experiment=self.experiment,
            workers=max(0, workers or 0),
            well_order=tuple(self.well_order),
        )


@dataclass
class SelectionTarget:
    """How many wells the file's metadata expects you to place, for the readout.

    The OME metadata fixes a total count — stage *positions* (multi-image) or
    *frames* (single-image) — and ``positions = wells × FOVs`` (a single-image
    file with timepoints folds those in too, so the position count isn't knowable
    from metadata until the frame-order splits them out). This carries that count
    and, when FOVs-per-well is pinned, the well count it implies, so the page can
    tell the user up front how many wells to select rather than only erroring at
    the end.
    """

    positions: int | None    # total stage positions the file holds, if knowable
    noun: str                # "stage position" | "frame" — for the wording
    fovs_fixed: int | None   # user-pinned FOVs/well, else None (auto/derive)

    @property
    def expected_wells(self) -> int | None:
        """The well count the position budget implies at the pinned FOVs, if any."""
        if self.positions is None or not self.fovs_fixed:
            return None
        if self.positions % self.fovs_fixed:
            return None
        return self.positions // self.fovs_fixed

    def hint(self) -> str:
        """A one-line, forward-looking target for the readout (no ✓/⚠ — the
        plan's own validation line carries the pass/fail)."""
        if self.positions is None:
            return (
                "↳ Frames pack positions × timepoints — set FOVs per well so "
                "positions = wells × FOVs and timepoints = frames ÷ positions."
            )
        exp = self.expected_wells
        if exp is not None:
            return (
                f"↳ Target: {exp} well(s) at {self.fovs_fixed} FOV(s)/well "
                f"({self.positions} {self.noun}(s))."
            )
        return (
            f"↳ Target: place {self.positions} {self.noun}(s) — select wells so "
            f"wells × FOVs = {self.positions}."
        )


def selection_target(layout: OmeLayout, params: ConversionParams) -> SelectionTarget:
    """Read the well-placement target from *layout* under *params*' frame-order.

    Qt-free so the page's readout stays a thin renderer over it (and it is
    unit-testable). See :class:`SelectionTarget` for what "knowable" means.
    """
    if not layout.single_image:
        positions, noun = layout.n_positions, "stage position"
    elif params.frame_order == "single":
        positions, noun = layout.n_frames, "frame"
    else:
        positions, noun = None, "frame"
    return SelectionTarget(positions, noun, params.fovs_per_well)


def strip_ome_suffix(name: str) -> str:
    """Return *name* without a trailing ``.ome.tif`` / ``.ome.tiff`` extension."""
    low = name.lower()
    for suf in _OME_SUFFIXES:
        if low.endswith(suf):
            return name[: -len(suf)]
    return Path(name).stem


def default_dest(src: Path | str) -> Path:
    """The default destination dataset folder for *src*: ``<parent>/<stem>_dataset``."""
    src = Path(src)
    return src.parent / f"{strip_ome_suffix(src.name)}_dataset"


@dataclass(frozen=True)
class LayoutSuggestion:
    """A plate layout read out of the file's own stage coordinates.

    ``well_order`` is filled only when the scan is **not** the serpentine the
    page can express with a grid — a raster, or any other path — because an
    explicit order is the only way to say those, whereas a serpentine is better
    left as grid fields the user can still edit.
    """

    rows: int
    cols: int
    fovs_per_well: int
    direction: str
    n_positions: int
    # Whether alternate lines run backwards. Read from the coordinates like the
    # rest, and applied to the page's Snake box: a raster offered as a snake
    # would fill the form with an order that mirrors every second line.
    serpentine: bool = True
    well_order: tuple[str, ...] = ()
    # How the file's flat frame sequence splits, read from the repeat pattern in
    # the coordinates — the one setting the file's own DimensionOrder cannot
    # express, because an OME Image has no position dimension.
    frame_order: str = ""
    n_timepoints: int = 0
    # The starting well, when the metadata names it. Coordinates are relative to
    # the microscope, so they can never say this; a StageLabel or Image name
    # often does.
    origin: str = ""

    def describe(self) -> str:
        path = " (as an explicit path)" if self.well_order else ""
        timing = (f", {self.n_timepoints} timepoint(s)"
                  if self.n_timepoints > 1 else "")
        placed = f", starting at {self.origin}" if self.origin else ""
        axis = "row" if self.direction == "row" else "column"
        sweep = f"snaking by {axis}" if self.serpentine else f"by {axis}, no snake"
        # A sparse block's extent is not its well count, and naming only the
        # extent claims wells the run never imaged — "3×3 wells" for an L of
        # five. Both numbers, so the shape is legible without the map.
        wells = len(self.well_order)
        shape = (f"{wells} wells in a {self.rows}×{self.cols} block"
                 if wells and wells != self.rows * self.cols
                 else f"{self.rows}×{self.cols} wells")
        return (
            f"{shape} × {self.fovs_per_well} FOV(s), "
            f"{sweep}{timing}{path}{placed}"
        )


def wells_from_names(names) -> list[str]:
    """The well each named position sits in, or ``[]`` if the names don't say.

    Acquisition software writes the well into a ``StageLabel`` or ``Image`` name
    far more often than not — ``"B02"``, ``"Well B2, Site 1"``, ``"scan_B02_f0"``
    — and that is the only thing in an OME-TIFF that locates a position on the
    *plate*. Every name has to yield exactly one well token for the answer to be
    usable: a set where some names parse and others do not is a partial mapping,
    and guessing the rest is how a plate ends up half-labelled.
    """
    out: list[str] = []
    for name in names or []:
        found = {canonical_well_label(token)
                 for token in re.findall(r"[A-Za-z]{1,2}\d{1,2}", str(name))}
        found.discard(None)
        if len(found) != 1:
            return []
        out.append(found.pop())
    return out


def infer_layout(layout: OmeLayout):
    """The plate layout the file's stage coordinates describe, or ``None``.

    The expensive half of the two calls below, and the reason they both take an
    already-computed one: it is a function of the *file*, while the page
    recomputes its readout on every keystroke in every box. Left to re-derive
    itself it ran twice per character typed — 190 ms of it on a 96-well plate at
    16 fields over 24 timepoints, on the UI thread.
    """
    return infer_stage_layout(layout.stage_positions,
                              in_um=layout.stage_positions_in_um)


def suggest_layout(layout: OmeLayout, origin: str = "A01",
                   *, inferred=None) -> LayoutSuggestion | None:
    """Read the plate layout out of *layout*'s stage coordinates, or ``None``.

    The OME-XML records where on the stage every plane was taken, and a plate
    scan's coordinates carry the well grid in their spacing (see
    :mod:`datasets.ome_import.stage_layout`). That answers, from the file
    itself, three of the questions the page otherwise asks the user to guess:
    the grid, the fields per well, and the direction the stage swept.

    *origin* places the inferred block, since stage coordinates are relative to
    the microscope and say nothing about which physical well the corner is.
    ``None`` when the file carries no positions or they do not describe a grid —
    a wrong suggestion silently entered into the form would be worse than none.
    """
    inferred = inferred if inferred is not None else infer_layout(layout)
    if inferred is None:
        return None
    # The block is *placed* at the origin the caller passes, always: an origin
    # the user has typed is an answer, and an inference never overrules one.
    # What the coordinates give is the origin to *offer* (``start`` below), which
    # the page applies into that same box on a form nobody has answered — so a
    # plate-referenced file still ends up placing itself, by filling the field
    # rather than by ignoring it.
    try:
        origin_row, origin_col = well_row_col(origin or "A01")
    except ValueError:
        origin_row, origin_col = 0, 0
    if inferred.absolute_origin is not None:
        origin_row, origin_col = (
            inferred.absolute_origin if not (origin or "").strip()
            else (origin_row, origin_col))
    # An explicit order whenever the grid fields could not reproduce this one.
    # "Is it a serpentine?" is not that question: a stage that reports Y (or X)
    # descending sweeps a perfectly good serpentine whose *first* well is the
    # block's bottom-left, while the grid always starts top-left. The rows come
    # out mirrored, every image lands one row off, and nothing on screen looks
    # wrong. Comparing the two orders outright covers that, the raster, and any
    # other starting corner, in one condition.
    placed = place(inferred, origin_row, origin_col)
    if not placed:
        return None
    # Compared at the origin the placement actually used — which the file may
    # have supplied — not at the one that was passed in. Comparing against a
    # serpentine drawn from a different corner makes every plate-referenced scan
    # look like a path the grid cannot express.
    start_label = f"{chr(ord('A') + origin_row)}{origin_col + 1:02d}"
    try:
        from_grid = block_well_labels(
            inferred.rows, inferred.cols,
            origin=start_label, direction=inferred.direction,
            serpentine=inferred.serpentine)
    except ValueError:
        from_grid = []
    order = () if placed == from_grid else tuple(placed)

    # The file's own account of where on the plate it was, when it has one. It
    # beats every inference here: the coordinates give a shape and the user
    # places it, while a StageLabel names the well outright.
    named = wells_from_names(layout.position_names)
    start = ""
    if inferred.absolute_origin is not None:
        r, c = inferred.absolute_origin
        start = f"{chr(ord('A') + r)}{c + 1:02d}"
    if named:
        visited = list(dict.fromkeys(named))
        start = visited[0]
        if len(visited) == inferred.rows * inferred.cols:
            order = tuple(visited)

    timing = infer_frame_order(layout.stage_positions) if layout.single_image else None
    return LayoutSuggestion(
        rows=inferred.rows,
        cols=inferred.cols,
        fovs_per_well=inferred.fovs_per_well,
        direction=inferred.direction,
        serpentine=inferred.serpentine,
        n_positions=inferred.n_positions,
        well_order=order,
        frame_order=(timing[0] if timing else ""),
        n_timepoints=(timing[2] if timing else 0),
        origin=start,
    )


def stage_path_for(layout: OmeLayout, anchor: str = "A01",
                   *, inferred=None) -> list[tuple[float, float]]:
    """The file's acquisition path in plate cells, starting at well *anchor*.

    Empty unless the coordinates describe a plate grid, since the cell scale is
    the well pitch and an unrecognised pattern has no reliable one — drawing at
    a guessed scale would put the fields of view in the wrong wells, which is
    exactly the mistake the picture exists to catch.

    **One cycle only.** A timelapse walks the same route once per timepoint, so
    its coordinates repeat; drawn in full that is one map overdrawn N times,
    with every dot on a dot and the numbering running to N times the count of
    real positions. ``single_cycle`` keeps the first arrival at each place, so
    what is drawn is the map of a single pass — see :func:`stage_path_cycles`
    for the count the caption reports.
    """
    inferred = inferred if inferred is not None else infer_layout(layout)
    if inferred is None or inferred.pitch <= 0:
        return []
    try:
        row, col = well_row_col(anchor or "A01")
    except ValueError:
        row, col = 0, 0
    return path_in_cells(single_cycle(layout.stage_positions),
                         inferred.pitch, row, col)


def oriented(layout: OmeLayout, *, flip_x: bool = False,
             flip_y: bool = False) -> OmeLayout:
    """*layout* with its stage coordinates reflected, or *layout* itself.

    Applied **once**, where the page reads the file, so that the inference, the
    suggested grid, the frame order, the drawn map and the well labels a
    conversion writes all read the same coordinates. Mirroring only the drawing
    would be the worse bug of the two it fixes: the picture the user checks the
    layout against would then disagree with the layout the run actually uses,
    and it would agree with it before the flip.

    Returns the object unchanged when neither axis flips, so the page's
    identity-keyed inference cache still sees one layout per file.
    """
    if not (flip_x or flip_y) or not layout.stage_positions:
        return layout
    return replace(
        layout,
        stage_positions=mirrored(layout.stage_positions,
                                 flip_x=flip_x, flip_y=flip_y),
    )


def stage_path_cycles(layout: OmeLayout) -> tuple[int, int]:
    """``(positions drawn, positions in the file)`` for the stage path.

    The caption needs both to say that what is on screen is one cycle of a
    repeated route rather than the whole file. A picture that quietly shows a
    twelfth of the coordinates is the silent-substitution failure CLAUDE.md §7
    is about, so the shortfall is named in the artifact rather than only being
    true.
    """
    positions = list(layout.stage_positions or ())
    return len(single_cycle(positions)), len(positions)


# Written into the destination while a conversion is running and removed when it
# finishes. Its presence is what separates "this tool's interrupted run, resume
# it" from "somebody else's dataset, refuse it" — the same device every other
# Datasets tool uses (``core.dataset_paths.make_dataset_api``).
IN_PROGRESS_MARKER = ".aw_ome_in_progress"

# The settings that decide which stage position becomes which well. Two runs
# agreeing on these produce identical archives; two that differ produce the same
# *filenames* from different images, which is why a resume has to compare them
# rather than trusting the names on disk.
_MAPPING_FIELDS = ("grid_rows", "grid_cols", "origin", "direction", "serpentine",
                   "fovs_per_well", "frame_order", "experiment", "well_order")


def _mapping_signature(src: Path | str, params: ConversionParams) -> dict:
    """What a resume has to match: the source, and the settings that map it."""
    return marker.mapping_signature(src, params, _MAPPING_FIELDS)


def write_run_marker(dest: Path | str, src: Path | str,
                     params: ConversionParams) -> None:
    """Record that a conversion is under way in *dest*, and with what settings."""
    marker.write_run_marker(dest, IN_PROGRESS_MARKER,
                            _mapping_signature(src, params))


def clear_run_marker(dest: Path | str) -> None:
    """Drop the marker — the run finished, so the folder is a dataset now."""
    marker.clear_run_marker(dest, IN_PROGRESS_MARKER, "ome-import")


def read_run_marker(dest: Path | str) -> dict | None:
    """The settings of the interrupted run in *dest*, or ``None`` if there is none."""
    return marker.read_run_marker(dest, IN_PROGRESS_MARKER)


def converted_wells(dest: Path | str) -> list[str]:
    """The well archives already sitting in ``<dest>/in``."""
    return marker.converted_wells(dest)


def resume_state(dest: Path | str, src: Path | str,
                 params: ConversionParams) -> ResumeState:
    """Read *dest* as a place to continue this conversion in."""
    return marker.resume_state(dest, IN_PROGRESS_MARKER,
                               _mapping_signature(src, params))


def destination_problem(dest: Path | str, src: Path | str = "",
                        params: ConversionParams | None = None) -> str:
    """``""`` if *dest* is usable, else a short reason, for the field's hint."""
    signature = (None if params is None
                 else _mapping_signature(src or dest, params))
    return marker.destination_problem(dest, IN_PROGRESS_MARKER, signature)


def read_layout(src: Path | str) -> OmeLayout:
    """Read the OME layout, converting any failure into an :class:`OmeImportError`."""
    try:
        return read_ome_layout(src)
    except OmeImportError:
        raise
    except Exception as exc:
        raise OmeImportError(
            f"Could not read {Path(src).name} as an OME-TIFF: {exc}"
        ) from exc


def plan_conversion(layout: OmeLayout, params: ConversionParams) -> ConversionPlan:
    """Resolve *params* against *layout* into a :class:`ConversionPlan`.

    Raises :class:`OmeImportError` when the grid, FOVs-per-well or frame-order
    can't produce a whole split, the grid overflows the 96-well plate, or the
    source declares image planes it does not contain — the last checked first,
    since no grid can make an incomplete file convertible, and the page's readout
    should say *that* rather than a divisibility error derived from it.
    """
    unsound = layout.validation_report()
    if unsound:
        raise OmeImportError(unsound)

    rows, cols = params.grid_rows, params.grid_cols
    if params.well_order:
        try:
            well_labels = normalize_well_order(params.well_order)
        except ValueError as exc:
            raise OmeImportError(str(exc)) from exc
    else:
        if rows <= 0 or cols <= 0:
            raise OmeImportError(
                "Enter the imaged well-grid dimensions (rows × columns) as positive "
                "whole numbers."
            )
        try:
            well_labels = block_well_labels(
                rows, cols, origin=params.origin or "A01",
                direction=params.direction, serpentine=params.serpentine,
            )
        except ValueError as exc:
            raise OmeImportError(str(exc)) from exc
    n_wells = len(well_labels)

    # How to name the well count in errors: the grid shape when it's an actual
    # rectangle, else the plain count (a click-defined order needn't be one).
    if params.well_order or rows * cols != n_wells:
        wells_desc = f"{n_wells} well(s)"
    else:
        wells_desc = f"{rows}×{cols} = {n_wells} well(s)"

    fovs = params.fovs_per_well
    if layout.single_image:
        n_positions, n_timepoints, fovs = _split_single_image(
            layout.n_frames, n_wells, fovs, params.frame_order, wells_desc,
        )
    else:
        n_positions, n_timepoints, fovs = _split_multi_image(
            layout.n_positions, n_wells, fovs, layout.size_t, wells_desc,
        )
    return ConversionPlan(
        single_image=layout.single_image,
        n_positions=n_positions,
        n_timepoints=n_timepoints,
        fovs_per_well=fovs,
        well_labels=well_labels,
        channels=list(layout.channel_names),
        hand_drawn=bool(params.well_order),
    )


def workable_splits(n_frames: int, limit: int = 6) -> list[tuple[int, int]]:
    """``(positions, timepoints)`` pairs a flat frame count can actually take.

    A refusal that only says the current grid does not fit leaves the user
    guessing at a number the file has already determined: the frame count's
    divisors are the *complete* list of ways it can split, and most counts have
    very few. 6958 frames, for instance, is 2 x 7 x 7 x 71 — it does not divide
    by 96, 48 or 24, so no full-plate reading exists at all, and saying which
    readings do exist turns a dead end into a choice.
    """
    if n_frames <= 0:
        return []
    pairs = [(d, n_frames // d) for d in range(1, min(n_frames, 96) + 1)
             if n_frames % d == 0]
    return pairs[:limit]


def _splits_hint(n_frames: int) -> str:
    """The workable splits as a sentence, or ``""`` when there is nothing to say."""
    pairs = workable_splits(n_frames)
    if not pairs:
        return ""
    shown = "; ".join(f"{pos} position(s) × {tps} timepoint(s)" for pos, tps in pairs)
    return f" This file's {n_frames} frames can only split as: {shown}."


def _split_multi_image(
    total: int, n_wells: int, fovs: int | None, size_t: int, wells_desc: str,
) -> tuple[int, int, int]:
    if fovs is None:
        if total % n_wells != 0:
            raise OmeImportError(
                f"{total} stage position(s) don't divide evenly into {wells_desc}. "
                f"Set FOVs per well, or fix the grid."
            )
        fovs = total // n_wells
    elif n_wells * fovs != total:
        raise OmeImportError(
            f"{wells_desc} × {fovs} FOV(s) = {n_wells * fovs}, but the file "
            f"has {total} stage position(s)."
        )
    return total, max(size_t, 1), fovs


def _split_single_image(
    n_frames: int, n_wells: int, fovs: int | None, order: str, wells_desc: str,
) -> tuple[int, int, int]:
    if order not in FRAME_ORDERS:
        raise OmeImportError(f"Unknown frame order {order!r}.")
    if order == "single":
        if fovs is None:
            if n_frames % n_wells != 0:
                raise OmeImportError(
                    f"{n_frames} frames don't divide evenly into {wells_desc}. "
                    f"Set FOVs per well, or fix the grid."
                    + _splits_hint(n_frames)
                )
            fovs = n_frames // n_wells
        if n_wells * fovs != n_frames:
            raise OmeImportError(
                f"{wells_desc} × {fovs} FOV(s) = {n_wells * fovs}, but the "
                f"file has {n_frames} frame(s)." + _splits_hint(n_frames)
            )
        return n_frames, 1, fovs
    if fovs is None:
        raise OmeImportError(
            "This file packs timepoints into the frame axis, so FOVs per well is "
            "needed to separate positions from timepoints (positions = wells × "
            "FOVs; timepoints = frames ÷ positions)."
        )
    n_positions = n_wells * fovs
    n_timepoints, rem = divmod(n_frames, n_positions)
    if rem or n_timepoints < 1:
        raise OmeImportError(
            f"{n_frames} frames don't split into {wells_desc} × {fovs} FOV(s) = "
            f"{n_positions} positions × a whole number of timepoints."
            + _splits_hint(n_frames)
        )
    return n_positions, n_timepoints, fovs
