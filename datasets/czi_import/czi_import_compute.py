"""Qt-free planning + validation for the CZI converter.

Reads a CZI's layout (via :mod:`pipeline.czi_read`) and resolves the user's
settings into a :class:`ConversionPlan` — the same mapping arithmetic the
explode performs, surfaced ahead of the run so the page can show a live readout
and refuse an inconsistent request before a single archive is written.

The one thing this page decides that the OME-TIFF page does not is **where the
well labels come from**. A CZI plate scan normally names each scene after the
well it was taken from, so the default is to read the mapping out of the file;
the grid is the fallback for a file that does not name its scenes. Which of the
two produced the answer is carried on the plan rather than inferred from the
settings, because the counts in the readout are identical either way and only
one of them is a mapping the user is entitled to trust without checking.

No Qt and no file writing beyond the resume marker, so all of it is unit-
testable without an event loop and the page/worker layer stays a thin shell on
top (CLAUDE.md §3/§5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from datasets.core import convert_marker as marker
from datasets.core.convert_marker import ResumeState  # noqa: F401 (re-export)
from pipeline.czi_explode import WELL_SOURCES, resolve_well_map
from pipeline.czi_read import CziReadError
from pipeline.czi_set import (
    CziSet,
    PlatePeek,
    discover_czi_files,
    peek_wells,
    read_czi_set_safely,
    siblings_of,
)
from util.channel_roles import (  # noqa: F401 (re-export for the view)
    CHANNEL_ROLES,
    channel_tokens_from_roles,
    default_channel_roles,
)

_CZI_SUFFIXES = (".czi",)

#: Written into the destination while a conversion is running and removed when
#: it finishes. Its own name, not the OME converter's: a folder holding the
#: other converter's leftovers is not this one's run to finish.
IN_PROGRESS_MARKER = ".aw_czi_in_progress"

#: The settings that decide which image lands under which filename. Two runs
#: agreeing on every one of these produce the same dataset, so the second may
#: finish the first; two that differ on any of them produce *identically named*
#: files from different images, which is the one thing a resume must never
#: stitch together. ``workers`` is deliberately absent — it changes how fast the
#: conversion goes and nothing about what it writes.
_MAPPING_FIELDS = ("well_source", "use_folder", "grid_rows", "grid_cols",
                   "origin", "direction", "serpentine", "fovs_per_well",
                   "experiment")


class CziImportError(Exception):
    """A user-facing reason the CZI can't be converted with these settings."""


@dataclass(frozen=True)
class ConversionParams:
    """The user's conversion settings from the CZI converter page."""

    #: ``"file"`` reads the well of each scene from the CZI's own metadata;
    #: ``"grid"`` ignores those names and lays the acquisition order over a
    #: rectangular block of the plate, as the OME-TIFF converter does.
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
    fovs_per_well: int | None = None      # None = take it from the file
    experiment: str = ""                  # blank = derive from the filename
    workers: int = 0                      # 0 = auto (per-well thread pool)
    #: Convert every ``.czi`` beside the selected one as a single plate. This
    #: is ZEN's *Split Scenes* export: one file per well, with nothing inside
    #: any of them saying it is part of a plate. Off for a lone file, so
    #: picking one file and converting its neighbours is never a surprise; the
    #: page turns it on when the folder really does look like one plate, and
    #: says so where the user can see it. Irrelevant when the selected path is
    #: itself a folder, which *is* the set.
    use_folder: bool = False


@dataclass
class ConversionPlan:
    """The resolved shape of a conversion, for the readout and a sanity gate."""

    n_scenes: int
    n_timepoints: int
    fovs_per_well: int
    #: How many ``.czi`` files this conversion reads. More than one is a
    #: split-scene export, and the readout says so — the same well count from
    #: one file and from ninety-six is two very different things to check.
    n_files: int = 1
    well_labels: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    #: True when ``well_labels`` came from the file's own scene names.
    from_file: bool = False

    @property
    def n_wells(self) -> int:
        return len(self.well_labels)

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
        held = f" across {self.n_files} file(s)" if self.n_files > 1 else ""
        images = (self.n_wells * self.fovs_per_well * self.n_timepoints
                  * max(1, len(self.channels)))
        return (
            f"{self.n_wells} well(s){span} ({source}){held} × "
            f"{self.fovs_per_well} FOV(s) × {self.n_timepoints} timepoint(s) × "
            f"{len(self.channels)} channel(s) ({channels}) — {images} image(s)."
        )


def parse_count(text: object) -> tuple[int | None, bool]:
    """``(value, ok)`` for a small positive integer typed into a field.

    Blank is ``(None, True)`` — a field left empty means "take it from the
    file", which is a valid answer and not an error. Anything else that is not
    a positive whole number is ``(None, False)``, so a caller can tell "not set"
    from "set to nonsense" rather than treating both as a default.
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
    "filled in wrongly" — the same split ``datasets.ome_import`` makes, and for
    the same reason: a page that treats an empty field as a zero tells the user
    their grid is invalid before they have finished typing it.
    """

    well_source: str = "file"
    use_folder: bool = False
    rows: str = ""
    cols: str = ""
    fovs: str = ""
    origin: str = ""
    workers: str = ""
    direction: str = "row"
    serpentine: bool = True
    experiment: str = ""

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
        return out

    def to_params(self) -> ConversionParams:
        """These fields as a :class:`ConversionParams`, defaults filled in."""
        rows, _ = parse_count(self.rows)
        cols, _ = parse_count(self.cols)
        fovs, _ = parse_count(self.fovs)
        workers, _ = parse_count(self.workers)
        return ConversionParams(
            well_source=self.well_source,
            use_folder=self.use_folder,
            grid_rows=rows or 0,
            grid_cols=cols or 0,
            origin=(self.origin or "A01").strip() or "A01",
            direction=self.direction,
            serpentine=self.serpentine,
            fovs_per_well=fovs,
            experiment=self.experiment.strip(),
            workers=workers or 0,
        )


def strip_czi_suffix(name: str) -> str:
    """``"Plate7.czi"`` → ``"Plate7"``; anything else is returned unchanged."""
    lowered = (name or "").lower()
    for suffix in _CZI_SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem or name


def default_dest(src: Path | str, params: ConversionParams | None = None) -> Path:
    """The folder a conversion of *src* writes into unless the user says otherwise.

    Beside the source and named after it, which is what makes the destination
    field a confirmation rather than a second thing to go and find. A single
    file drops its ``.czi``, which is what keeps it beside rather than on top
    of itself.

    For a **set** the source is the folder, which has no extension to drop —
    so it takes the folder's name plus ``_dataset``. Naming it after whichever
    member sorted first would bury a whole plate under one scene's name, and
    the folder's bare name *is the folder*: this returned the source folder
    itself, which would have written ``in/`` in among the very ``.czi`` files
    the plate was being read from.
    """
    src = Path(src)
    if src.is_dir():
        return src.parent / f"{src.name}_dataset"
    if params is not None and params.use_folder and len(source_files(src, params)) > 1:
        folder = src.parent
        return folder.parent / f"{folder.name}_dataset"
    return src.parent / strip_czi_suffix(src.name)


def source_files(src: Path | str, params: ConversionParams) -> list[Path]:
    """Exactly the ``.czi`` files a run with these settings converts.

    One definition, consulted by the page's readout, by the executor and by the
    resume marker — because a run that converts a different set of files from
    the one the readout described, or resumes against a set the marker never
    saw, is the same defect wearing three hats.
    """
    src = Path(src)
    if src.is_dir():
        # A folder *is* the set; the tick-box only ever widens a lone file.
        return discover_czi_files(src)
    if not src.is_file():
        return []
    return siblings_of(src) if params.use_folder else [src]


def peek_source(src: Path | str, params: ConversionParams) -> PlatePeek:
    """The wells *params*'s files claim, from their headers alone.

    What the page draws its plate from while the full read is still running.
    A few hundred bytes to a few kilobytes per file against the ~1 MB of
    subblock directory a full read walks, and no pixels either way — which on a
    plate held on a network share is the difference between a plate map
    appearing at once and one appearing after every file has been round-tripped.
    Provisional by construction; see :class:`~pipeline.czi_set.PlatePeek`.
    """
    return peek_wells(src, files=source_files(src, params))


def read_source(src: Path | str, params: ConversionParams) -> CziSet:
    """Read the files *params* selects as one plate.

    Never raises for a source that cannot be used — the page calls this on
    every keystroke, and a traceback in a readout helps nobody. An unreadable
    or mismatched set comes back with :attr:`~pipeline.czi_set.CziSet.problem`
    set; :func:`plan_conversion` is what turns that into a
    :class:`CziImportError` at the point a caller is entitled to one.
    """
    try:
        return read_czi_set_safely(src, files=source_files(src, params))
    except (CziReadError, OSError) as exc:      # pragma: no cover — defensive
        return CziSet(problem=str(exc))


def plan_conversion(source: CziSet, params: ConversionParams) -> ConversionPlan:
    """Resolve *params* against *source* into a :class:`ConversionPlan`.

    Raises :class:`CziImportError` when a file is unsound, when two files of a
    set disagree about something the filenames depend on, when the well source
    cannot produce a mapping, or when the grid and the files disagree about how
    many positions there are. Soundness comes first: no grid can make an
    incomplete file convertible, so the readout should say *that* rather than a
    divisibility error derived from it.
    """
    if source.problem:
        raise CziImportError(source.problem)
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
        raise CziImportError(str(exc)) from exc
    return ConversionPlan(
        n_scenes=source.n_scenes,
        n_timepoints=source.layout.size_t,
        fovs_per_well=well_map.fovs_per_well,
        n_files=source.n_files,
        well_labels=list(well_map.wells),
        channels=list(source.layout.channel_names),
        from_file=well_map.from_file,
    )


# ── Resume marker (shared with the OME converter — datasets.core.convert_marker)

def _signature(src: Path | str, params: ConversionParams) -> dict:
    """The mapping signature, plus the identity of *every* file in the set.

    The shared helper fingerprints one source path, which is the whole story
    for a single-file conversion and only the first line of it for a
    split-scene one: a sibling added, removed or re-exported between two runs
    changes which images land in which wells while the selected file's own
    path, size and mtime are untouched. Resuming across that stitches one
    folder out of two acquisitions, which is exactly what the marker exists to
    refuse.
    """
    values = marker.mapping_signature(src, params, _MAPPING_FIELDS)
    fingerprints = []
    for path in source_files(src, params):
        try:
            stat = path.stat()
            fingerprints.append([path.name, stat.st_size, stat.st_mtime_ns])
        except OSError:
            fingerprints.append([path.name, None, None])
    values["source_set"] = fingerprints
    return values


def write_run_marker(dest: Path | str, src: Path | str,
                     params: ConversionParams) -> None:
    """Record that a conversion is under way in *dest*, and with what settings."""
    marker.write_run_marker(dest, IN_PROGRESS_MARKER, _signature(src, params))


def clear_run_marker(dest: Path | str) -> None:
    """Drop the marker — the run finished, so the folder is a dataset now."""
    marker.clear_run_marker(dest, IN_PROGRESS_MARKER, "czi-import")


def read_run_marker(dest: Path | str) -> dict | None:
    """The settings of the interrupted run in *dest*, or ``None`` if there is none."""
    return marker.read_run_marker(dest, IN_PROGRESS_MARKER)


def converted_wells(dest: Path | str) -> list[str]:
    """The well archives already sitting in ``<dest>/in``."""
    return marker.converted_wells(dest)


def _signature_if_resumable(dest: Path | str, src: Path | str,
                            params: ConversionParams | None) -> dict | None:
    """This run's signature, but only when *dest* holds a marker to compare it to.

    Building the signature stats **every file in the set**, which is the right
    cost when there is an interrupted run to match and pure waste when there is
    not. Both callers below run on the page's readout path, which fires on
    every keystroke, and a destination with no marker is the ordinary case —
    so on a 96-file plate held on a network share this was ninety-six round
    trips per character typed. The marker's own existence is one stat.
    """
    if params is None:
        return None
    if marker.read_run_marker(dest, IN_PROGRESS_MARKER) is None:
        return None
    return _signature(src or dest, params)


def resume_state(dest: Path | str, src: Path | str,
                 params: ConversionParams) -> ResumeState:
    """Read *dest* as a place to continue this conversion in."""
    signature = _signature_if_resumable(dest, src, params)
    if signature is None:
        return ResumeState()
    return marker.resume_state(dest, IN_PROGRESS_MARKER, signature)


def destination_problem(dest: Path | str, src: Path | str = "",
                        params: ConversionParams | None = None) -> str:
    """``""`` if *dest* is usable, else a short reason, for the field's hint."""
    return marker.destination_problem(
        dest, IN_PROGRESS_MARKER,
        _signature_if_resumable(dest, src, params))
