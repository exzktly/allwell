"""Qt-free planning + validation for the ND2 converter.

Reads an ND2's layout (via :mod:`pipeline.nd2_read`) and resolves the user's
settings into a :class:`ConversionPlan` — the same mapping arithmetic the
explode performs, surfaced ahead of the run so the page can show a live
readout and refuse an inconsistent request before a single archive is written.

Shorter than its CZI counterpart for two reasons. An ND2 is always **one
file**, so there is no set to discover, no folder tick-box and no
whole-plate-versus-split-scene question; and the fields per well are something
the file states rather than something the user divides out, because several
positions naming one well *are* that well's fields of view. What is left that
the OME-TIFF converter does not have is the same one decision the CZI
converter makes: where the well labels come from.

No Qt and no file writing beyond the resume marker, so all of it is
unit-testable without an event loop and the page stays a thin shell on top
(CLAUDE.md §3/§5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from datasets.core import convert_marker as marker
from datasets.core.convert_marker import ResumeState  # noqa: F401 (re-export)
from pipeline.explode_common import timepoint_plan, wells_to_convert
from pipeline.nd2_explode import WELL_SOURCES, resolve_well_map, timepoint_tokens
from pipeline.nd2_read import Nd2Layout, Nd2ReadError, read_nd2_layout
from util.channel_roles import (  # noqa: F401 (re-export for the view)
    CHANNEL_ROLES,
    channel_tokens_from_roles,
    default_channel_roles,
)

_ND2_SUFFIXES = (".nd2",)

#: Written into the destination while a conversion is running and removed when
#: it finishes. Its own name, not another converter's: a folder holding a
#: different tool's leftovers is not this one's run to finish.
IN_PROGRESS_MARKER = ".aw_nd2_in_progress"

#: The settings that decide which image lands under which filename. Two runs
#: agreeing on every one of these produce the same dataset, so the second may
#: finish the first; two that differ on any of them produce *identically
#: named* files from different images, which is the one thing a resume must
#: never stitch together. ``workers`` is deliberately absent — it changes how
#: fast the conversion goes and nothing about what it writes.
_MAPPING_FIELDS = ("well_source", "grid_rows", "grid_cols", "origin",
                   "direction", "serpentine", "fovs_per_well", "experiment",
                   "timepoint_stride")


class Nd2ImportError(Exception):
    """A user-facing reason the ND2 can't be converted with these settings."""


@dataclass(frozen=True)
class ConversionParams:
    """The user's conversion settings from the ND2 converter page."""

    #: ``"file"`` reads each position's well from its own name, which is what
    #: a Nikon plate scan records; ``"grid"`` ignores those names and lays the
    #: acquisition order over a rectangular block of the plate, as the
    #: OME-TIFF converter does.
    well_source: str = "file"
    grid_rows: int = 0
    grid_cols: int = 0
    origin: str = "A01"
    direction: str = "row"
    # False rasters the grid: every line swept the same way, the stage flying
    # back between them. A raster read as a snake mislabels every second line
    # backwards, with the well count still correct — so it is an input, not an
    # assumption.
    serpentine: bool = True
    #: ``None`` takes it from the file. Only a grid-mapped file needs this:
    #: when the positions name their wells, the file has already said how many
    #: fields each well takes and a contradicting value is refused rather than
    #: silently preferred.
    fovs_per_well: int | None = None
    #: Convert every N-th timepoint instead of all of them, counting from the
    #: first. 1 — the default — converts the whole timelapse. Part of the
    #: mapping signature above and not merely a speed setting: a well left
    #: alone by a resume because a previous run at stride 1 already filled it
    #: would hold every timepoint while the wells beside it held a third of
    #: them, and ``well_is_complete`` tests a *subset*, so nothing downstream
    #: would notice the plate had gone ragged.
    timepoint_stride: int = 1
    experiment: str = ""                  # blank = derive from the filename
    #: Convert only these wells, leaving the rest of the plate alone; empty
    #: converts all of them. Canonical labels (``A01``), matched case- and
    #: padding-insensitively against the mapping the other settings resolve.
    #:
    #: **Deliberately not part of the resume signature below.** A well's
    #: archive holds exactly the same images whether or not the wells beside
    #: it were converted in the same run, so the selection changes nothing
    #: about which image lands under which filename — the one question that
    #: signature answers. Keying on it would refuse the workflow a subset
    #: exists for: convert six wells today, the rest next week, one dataset.
    well_subset: tuple[str, ...] = ()
    workers: int = 0                      # 0 = auto (per-well thread pool)


@dataclass
class ConversionPlan:
    """The resolved shape of a conversion, for the readout and a sanity gate."""

    n_positions: int
    #: Timepoints a run would *write* — after the stride, not before it.
    n_timepoints: int
    fovs_per_well: int
    #: Timepoints the source holds. Equal to ``n_timepoints`` unless a stride
    #: is dropping some, and kept apart from it so the readout can say which
    #: of the two a number is.
    n_source_timepoints: int = 0
    #: The kept timepoints' own tokens, in order — never renumbered.
    timepoint_tokens: list[str] = field(default_factory=list)
    #: What the user should be told about the timepoint names — a whole
    #: sentence, or ``""``.
    timepoint_note: str = ""
    well_labels: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    #: The wells this run will actually write, when a subset was chosen; empty
    #: means the whole mapping. Kept beside ``well_labels`` rather than
    #: replacing it, because the readout has to state both: which wells the
    #: *source* holds, and which of them this run covers.
    selected_wells: list[str] = field(default_factory=list)
    #: True when ``well_labels`` came from the positions' own names.
    from_file: bool = False

    @property
    def n_wells(self) -> int:
        return len(self.well_labels)

    @property
    def timepoint_phrase(self) -> str:
        """``"12 timepoint(s)"``, or ``"4 of 12 timepoint(s) (0h, 12h, …)"``.

        A stride makes the plain count a half-truth: the run writes a complete
        dataset that silently holds a third of the acquisition, and the number
        it would otherwise print is indistinguishable from a plate that only
        ever had four timepoints (CLAUDE.md §7).
        """
        if (not self.n_source_timepoints
                or self.n_timepoints >= self.n_source_timepoints):
            return f"{self.n_timepoints} timepoint(s)"
        shown = ", ".join(self.timepoint_tokens[:4])
        if len(self.timepoint_tokens) > 4:
            shown += f", … {self.timepoint_tokens[-1]}"
        return (f"{self.n_timepoints} of {self.n_source_timepoints} "
                f"timepoint(s) ({shown})")

    @property
    def timepoint_warning(self) -> str:
        """The sentence the page shows about how the timepoints were named.

        Said *before* the run rather than only in the log afterwards. A plate
        whose timepoints lost their elapsed hours is a dataset with no time
        axis in Review, and the moment to find that out is while the source
        can still be re-exported.
        """
        return self.timepoint_note

    def summary(self) -> str:
        """A one-line description of what a run would produce.

        Names the wells at both ends of the mapping, not only how many there
        are: a count alone reads the same whether the mapping starts where the
        user meant it to or one well earlier, which is exactly the mistake a
        grid can make. And it says which source produced it, because that is
        the difference between a mapping to check and a mapping to trust.
        """
        channels = ", ".join(self.channels) if self.channels else "?"
        span = ""
        if self.well_labels:
            first, last = self.well_labels[0], self.well_labels[-1]
            span = f" {first}" if first == last else f" {first}→{last}"
        source = ("named by the file" if self.from_file else "from the plate grid")
        # Counted over the wells this run writes. Denominating by the whole
        # mapping described a subset conversion as the plate it was taken from
        # — "3 well(s) … 6 image(s)" above a run that wrote two wells and four
        # images — which is the same lie the run log's own count was fixed
        # against, on the surface the user actually reads before pressing
        # Convert.
        n_writing = len(self.selected_wells) or self.n_wells
        images = (n_writing * self.fovs_per_well * self.n_timepoints
                  * max(1, len(self.channels)))
        wells_phrase = (f"{self.n_wells} well(s){span}" if not self.selected_wells
                        else f"{n_writing} of {self.n_wells} well(s){span}, "
                             f"selected")
        return (
            f"{wells_phrase} ({source}) × "
            f"{self.fovs_per_well} FOV(s) × {self.timepoint_phrase} × "
            f"{len(self.channels)} channel(s) ({channels}) — {images} image(s)."
        )


def parse_count(text: object) -> tuple[int | None, bool]:
    """``(value, ok)`` for a small positive integer typed into a field.

    Blank is ``(None, True)`` — a field left empty means "take it from the
    file", which is a valid answer and not an error. Anything else that is not
    a positive whole number is ``(None, False)``, so a caller can tell "not
    set" from "set to nonsense" rather than treating both as a default.
    """
    raw = str(text or "").strip()
    if not raw:
        return None, True
    try:
        value = int(raw)
    except ValueError:
        return None, False
    return (value, True) if value > 0 else (None, False)


@dataclass(frozen=True)
class RawFields:
    """The page's fields exactly as typed, before any of them is trusted.

    Kept as strings so the readout can distinguish "not filled in yet" from
    "filled in wrongly" — the same split the other two converters make, and
    for the same reason: a page that treats an empty field as a zero tells the
    user their grid is invalid before they have finished typing it.
    """

    well_source: str = "file"
    rows: str = ""
    cols: str = ""
    fovs: str = ""
    origin: str = ""
    workers: str = ""
    timepoint_stride: str = ""
    direction: str = "row"
    serpentine: bool = True
    experiment: str = ""
    #: Wells the user picked on the plate; empty converts all of them. Kept as
    #: given rather than validated here — a well the source does not hold is
    #: refused by `explode_common.wells_to_convert` with a message naming it,
    #: which is where the plate's real labels are known.
    well_subset: tuple[str, ...] = ()

    def problems(self) -> list[str]:
        """Everything wrong with the fields as they stand, in reading order."""
        out: list[str] = []
        if self.well_source not in WELL_SOURCES:
            out.append(f"Unknown well source {self.well_source!r}.")
        if self.well_source == "grid":
            rows, rows_ok = parse_count(self.rows)
            cols, cols_ok = parse_count(self.cols)
            if not rows_ok or not cols_ok:
                out.append("Grid rows and columns must be positive whole numbers.")
            elif rows is None or cols is None:
                out.append("Enter the imaged well-grid rows and columns.")
        if not parse_count(self.fovs)[1]:
            out.append("FOVs per well must be a positive whole number, or blank "
                       "to take it from the file.")
        if not parse_count(self.workers)[1]:
            out.append("Worker threads must be a positive whole number, or "
                       "blank for automatic.")
        if not parse_count(self.timepoint_stride)[1]:
            out.append("Keep every Nth timepoint must be a positive whole "
                       "number, or blank to convert every timepoint.")
        return out

    def to_params(self) -> ConversionParams:
        """These fields as a :class:`ConversionParams`, defaults filled in."""
        rows, _ = parse_count(self.rows)
        cols, _ = parse_count(self.cols)
        fovs, _ = parse_count(self.fovs)
        workers, _ = parse_count(self.workers)
        stride, _ = parse_count(self.timepoint_stride)
        return ConversionParams(
            well_source=self.well_source,
            grid_rows=rows or 0,
            grid_cols=cols or 0,
            origin=(self.origin or "A01").strip() or "A01",
            direction=self.direction,
            serpentine=self.serpentine,
            fovs_per_well=fovs,
            timepoint_stride=stride or 1,
            experiment=self.experiment.strip(),
            well_subset=tuple(self.well_subset),
            workers=workers or 0,
        )


def strip_nd2_suffix(name: str) -> str:
    """``"Plate7.nd2"`` → ``"Plate7"``; anything else is returned unchanged."""
    lowered = (name or "").lower()
    for suffix in _ND2_SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem or name


def default_dest(src: Path | str) -> Path:
    """The folder a conversion of *src* writes into unless the user says otherwise.

    Beside the source and named after it, which is what makes the destination
    field a confirmation rather than a second thing to go and find.
    """
    src = Path(src)
    return src.parent / strip_nd2_suffix(src.name)


def read_source(src: Path | str, params: ConversionParams | None = None) -> Nd2Layout:
    """Read *src*'s layout, with the times, for the page's readout.

    Never raises for a source that cannot be used — the page calls this on
    every keystroke, and a traceback in a readout helps nobody. An unreadable
    file comes back as an empty layout whose :meth:`validation_report` says so;
    :func:`plan_conversion` is what turns that into an :class:`Nd2ImportError`
    at the point a caller is entitled to one.

    ``params`` is accepted and ignored, so the reader matches the shape
    :class:`datasets.core.packed_convert.ConverterSpec` calls every converter's
    with — an ND2 is one file and its layout does not depend on the settings.
    """
    del params
    try:
        return read_nd2_layout(src, read_times=True)
    except (Nd2ReadError, OSError) as exc:
        layout = Nd2Layout()
        layout.warnings.append(str(exc))
        return layout


def plan_conversion(source: Nd2Layout, params: ConversionParams) -> ConversionPlan:
    """Resolve *params* against *source* into a :class:`ConversionPlan`.

    Raises :class:`Nd2ImportError` when the file is unsound, when the well
    source cannot produce a mapping, or when the grid and the file disagree
    about how many positions there are. Soundness comes first: no grid can
    make an unreadable file convertible, so the readout should say *that*
    rather than a divisibility error derived from it.
    """
    if source.warnings and not source.n_positions:
        raise Nd2ImportError(source.warnings[0])
    unsound = source.validation_report("the file")
    if unsound:
        raise Nd2ImportError(unsound)
    try:
        well_map = resolve_well_map(
            source,
            well_source=params.well_source,
            grid_rows=params.grid_rows,
            grid_cols=params.grid_cols,
            origin=params.origin or "A01",
            direction=params.direction,
            serpentine=params.serpentine,
            fovs_per_well=params.fovs_per_well,
        )
    except ValueError as exc:
        raise Nd2ImportError(str(exc)) from exc
    # Resolved here rather than at run time so a well the source does not
    # hold is refused on the page, where the plate showing the real labels is
    # two inches away — not after the run has started.
    try:
        chosen = wells_to_convert(well_map.wells, params.well_subset)
    except ValueError as exc:
        raise Nd2ImportError(str(exc)) from exc
    selected_labels = ([] if not params.well_subset
                       else [well_map.wells[i] for i in chosen])
    tokens, note = timepoint_tokens(source)
    try:
        kept = timepoint_plan(tokens, params.timepoint_stride)
    except ValueError as exc:
        raise Nd2ImportError(str(exc)) from exc
    return ConversionPlan(
        n_positions=source.n_positions,
        n_timepoints=len(kept),
        n_source_timepoints=source.size_t,
        timepoint_tokens=[token for _index, token in kept],
        timepoint_note=note,
        fovs_per_well=well_map.fovs_per_well,
        well_labels=list(well_map.wells),
        channels=list(source.channel_names),
        selected_wells=selected_labels,
        from_file=well_map.from_file,
    )


# ── Resume marker (shared with the other converters — datasets.core.convert_marker)

def write_run_marker(dest: Path | str, src: Path | str,
                     params: ConversionParams) -> None:
    """Record that a conversion is under way in *dest*, and with what settings."""
    marker.write_run_marker(dest, IN_PROGRESS_MARKER,
                            marker.mapping_signature(src, params, _MAPPING_FIELDS))


def write_done_marker(dest: Path | str, src: Path | str,
                      params: ConversionParams) -> None:
    """Record the mapping this finished conversion used.

    What lets a later run add the wells this one did not convert: without it a
    top-up is indistinguishable from a stranger's plate pointed at the same
    folder, which the destination check refuses outright.
    """
    marker.write_done_marker(
        dest, marker.mapping_signature(src, params, _MAPPING_FIELDS))


def clear_run_marker(dest: Path | str) -> None:
    """Drop the marker — the run finished, so the folder is a dataset now."""
    marker.clear_run_marker(dest, IN_PROGRESS_MARKER, "nd2-import")


def read_run_marker(dest: Path | str) -> dict | None:
    """The settings of the interrupted run in *dest*, or ``None`` if there is none."""
    return marker.read_run_marker(dest, IN_PROGRESS_MARKER)


def converted_wells(dest: Path | str) -> list[str]:
    """The well archives already sitting in ``<dest>/in``."""
    return marker.converted_wells(dest)


def resume_state(dest: Path | str, src: Path | str,
                 params: ConversionParams) -> ResumeState:
    """Read *dest* as a place to continue this conversion in."""
    return marker.resume_state(
        dest, IN_PROGRESS_MARKER,
        marker.mapping_signature(src, params, _MAPPING_FIELDS))


def destination_problem(dest: Path | str, src: Path | str = "",
                        params: ConversionParams | None = None) -> str:
    """Why *dest* cannot be written into, or ``""``.

    Checked before a run because a conversion writes ``<dest>/in/<WELL>.zip``
    and overwrites each archive it produces: a destination already holding a
    plate ends up holding two of them with nothing in it saying so, and the
    ``out/`` measurements beside them then describe inputs that are no longer
    there.
    """
    # The signature is built only when the folder carries one of this tool's
    # markers, because building it fingerprints the source — a stat per file,
    # which on a split export is ninety-six of them, on a path a keystroke
    # takes. Both markers are checked: the in-progress one says "finish this
    # run", and the completed one says "this is the dataset you built, add the
    # wells you skipped". Consulting only the first was a real gap — a
    # finished run leaves no in-progress marker, so a subset top-up was
    # refused as a stranger's plate.
    signature = None
    if params is not None and (
            marker.read_run_marker(dest, IN_PROGRESS_MARKER) is not None
            or marker.read_done_marker(dest) is not None):
        signature = marker.mapping_signature(src or dest, params, _MAPPING_FIELDS)
    return marker.destination_problem(dest, IN_PROGRESS_MARKER, signature)
