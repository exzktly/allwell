"""Qt-free planning + validation for the OME-TIFF converter.

Reads the OME layout (via :mod:`pipeline.ome_explode`) and resolves the user's
well-grid, FOVs-per-well and frame-order into a :class:`ConversionPlan` — the
same split maths the explode uses, surfaced ahead of the run so the page can show
a live readout and block an inconsistent request before any file is written.

No Qt, no file writing — so it is unit-testable without an event loop, and the
page/worker layer stays a thin shell on top (CLAUDE.md §3/§5).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from util.aw_safe import swallow

from pipeline.ome_explode import (
    FRAME_ORDERS,
    OmeLayout,
    read_ome_layout,
)
from datasets.ome_import.stage_layout import (
    infer_frame_order,
    infer_stage_layout,
    path_in_cells,
    place,
)
from util.plate_grid import (
    normalize_well_order,
    serpentine_well_labels,
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
        return (
            f"{self.rows}×{self.cols} wells × {self.fovs_per_well} FOV(s), "
            f"snaking by {'row' if self.direction == 'row' else 'column'}"
            f"{timing}{path}{placed}"
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
    return infer_stage_layout(layout.stage_positions)


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
        from_grid = serpentine_well_labels(
            inferred.rows, inferred.cols,
            origin=start_label, direction=inferred.direction)
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
    """
    inferred = inferred if inferred is not None else infer_layout(layout)
    if inferred is None or inferred.pitch <= 0:
        return []
    try:
        row, col = well_row_col(anchor or "A01")
    except ValueError:
        row, col = 0, 0
    return path_in_cells(layout.stage_positions, inferred.pitch, row, col)


# Written into the destination while a conversion is running and removed when it
# finishes. Its presence is what separates "this tool's interrupted run, resume
# it" from "somebody else's dataset, refuse it" — the same device every other
# Datasets tool uses (``_common.dataset_paths.make_dataset_api``).
IN_PROGRESS_MARKER = ".aw_ome_in_progress"

# The settings that decide which stage position becomes which well. Two runs
# agreeing on these produce identical archives; two that differ produce the same
# *filenames* from different images, which is why a resume has to compare them
# rather than trusting the names on disk.
_MAPPING_FIELDS = ("grid_rows", "grid_cols", "origin", "direction",
                   "fovs_per_well", "frame_order", "experiment", "well_order")


def _mapping_signature(src: Path | str, params: ConversionParams) -> dict:
    """What a resume has to match: the source, and the settings that map it."""
    values = {name: getattr(params, name) for name in _MAPPING_FIELDS}
    values["well_order"] = list(values["well_order"] or ())
    source = Path(src)
    values["source"] = str(source.resolve())
    # Size and mtime as well as the path: a file replaced at the same name — a
    # re-export, a restore, the next plate saved over the last — is a different
    # source, and resuming across that leaves half of one plate and half of
    # another under one set of filenames. Cheap: one stat, no hashing.
    try:
        stat = source.stat()
        values["source_size"] = stat.st_size
        values["source_mtime_ns"] = stat.st_mtime_ns
    except OSError:
        values["source_size"] = None
        values["source_mtime_ns"] = None
    return values


def write_run_marker(dest: Path | str, src: Path | str,
                     params: ConversionParams) -> None:
    """Record that a conversion is under way in *dest*, and with what settings."""
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    (root / IN_PROGRESS_MARKER).write_text(
        json.dumps(_mapping_signature(src, params), indent=2), encoding="utf-8")


def clear_run_marker(dest: Path | str) -> None:
    """Drop the marker — the run finished, so the folder is a dataset now."""
    with swallow("ome-import marker"):
        (Path(dest) / IN_PROGRESS_MARKER).unlink(missing_ok=True)


def read_run_marker(dest: Path | str) -> dict | None:
    """The settings of the interrupted run in *dest*, or ``None`` if there is none."""
    path = Path(dest) / IN_PROGRESS_MARKER
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def converted_wells(dest: Path | str) -> list[str]:
    """The well archives already sitting in ``<dest>/in``."""
    in_dir = Path(dest) / "in"
    if not in_dir.is_dir():
        return []
    return sorted(p.stem for p in in_dir.glob("*.zip"))


@dataclass(frozen=True)
class ResumeState:
    """Whether *dest* holds an interrupted run of this conversion to finish."""

    resumable: bool = False
    wells_done: int = 0
    # A previous run is there but was making a different dataset — different
    # source, grid, origin or ordering. Resuming onto it would leave a folder
    # stitched from two mappings, with the filenames of one and the images of
    # both, and nothing in it saying so.
    mismatched: bool = False

    def hint(self) -> str:
        if self.mismatched:
            return ("This folder holds an interrupted conversion made with "
                    "different settings. Empty it, or choose another folder.")
        if self.resumable:
            return (f"↳ Resuming an interrupted run: {self.wells_done} well(s) "
                    f"are already converted and will be kept.")
        return ""


def resume_state(dest: Path | str, src: Path | str,
                 params: ConversionParams) -> ResumeState:
    """Read *dest* as a place to continue this conversion in."""
    marker = read_run_marker(dest)
    if marker is None:
        return ResumeState()
    done = len(converted_wells(dest))
    if marker != _mapping_signature(src, params):
        # A marker with no wells behind it describes a run that wrote nothing —
        # one that failed before its first archive, or was cancelled and then
        # had its settings changed, which is the commonest reason to cancel.
        # There is nothing on disk for a second mapping to contaminate, and the
        # marker is a hidden file the "empty the folder" advice does not clear.
        return ResumeState() if done == 0 else ResumeState(mismatched=True,
                                                           wells_done=done)
    return ResumeState(resumable=True, wells_done=done)


def destination_problem(dest: Path | str, src: Path | str = "",
                        params: ConversionParams | None = None) -> str:
    """``""`` if *dest* is usable, else a short reason, for the field's hint.

    A conversion writes ``<dest>/in/<WELL>.zip`` and overwrites each archive it
    produces, so a destination that already holds wells keeps whichever of them
    this run does not write — a folder holding two different plates, with
    nothing in it saying so. Every other Datasets tool validates its destination
    field this way (``_common.dataset_fields.is_free_destination``); the wording
    differs here only because this tool writes ``in/`` and nothing else.

    Given *src* and *params*, a folder carrying this tool's own in-progress
    marker for the *same* settings is not a problem but a resume, and one
    carrying it for different settings is refused with its own wording — the
    filenames of two mappings are identical, so only the marker can tell them
    apart.
    """
    root = Path(dest)
    if not str(dest).strip():
        return ""
    if root.exists() and not root.is_dir():
        return "Not a folder — choose a folder."
    if params is not None:
        state = resume_state(root, src or root, params)
        if state.mismatched:
            return state.hint()
        if state.resumable:
            return ""      # this tool's own interrupted run: finish it
    if (root / "out" / "pipeline_info.json").exists():
        return "Already holds a finished dataset — choose an empty folder."
    in_dir = root / "in"
    if in_dir.is_dir() and any(in_dir.glob("*.zip")):
        return "Already holds converted wells — choose an empty folder."
    return ""


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
            well_labels = serpentine_well_labels(
                rows, cols, origin=params.origin or "A01", direction=params.direction,
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
