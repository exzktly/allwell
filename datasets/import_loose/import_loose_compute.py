"""Pure preview scanner for the Import Loose Folder feature.

Mirrors ``well_plate_zipper._extract_well_from_filename``'s rule exactly —
schema-position lookup, ``canonical_well_label``, and ``os.path.isfile`` to
drop dangling symlinks the way ``os.walk`` lists them — so this preview and
the executor (which shells out to ``pipeline/well_plate_zipper.py`` for the in-place
mode, or writes zips directly for the new-destination mode) can never
disagree about what counts as a match. It deliberately does **not** call
``well_plate_zipper.group_by_well`` directly: that function ``print()``s
unconditionally (a scan banner, a per-5000-files progress line, a completion
line) to give the Analyze log something to show during a long directory
walk, and calling it in-process here would pollute this app's own captured
output stream with lines meant for a subprocess's stdout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from util.schema import parse_schema
from util.well_token import canonical_well_label

_IMAGE_SUFFIXES = (".tif", ".tiff")


def _well_index(schema: list[str]) -> int | None:
    try:
        return schema.index("well")
    except ValueError:
        return None


def _field_index(schema: list[str], field_name: str) -> int | None:
    try:
        return schema.index(field_name)
    except ValueError:
        return None


@dataclass(frozen=True)
class ImportPreview:
    """What a loose-TIF folder will convert into, without writing anything."""

    wells: tuple[str, ...] = ()
    fovs: tuple[str, ...] = ()
    timepoints: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    examined: int = 0
    matched: int = 0
    unreadable_count: int = 0
    unparsed_files: tuple[str, ...] = field(default_factory=tuple)
    #: Images found *below* the top level when the top level held none. Only
    #: counted in that case — it is the difference between "your schema is
    #: wrong" and "your images are one folder further down", and nothing else
    #: on the page can tell those apart.
    nested_count: int = 0
    #: The parsed schema this preview was taken with, so the summary can leave
    #: out the fields the schema never mentioned instead of reporting a count
    #: for them.
    schema_fields: tuple[str, ...] = field(default_factory=tuple)
    #: Well-named subfolders already sitting at the top level that hold TIFs.
    #: The in-place mode converts **every** well folder it finds — including
    #: ones it did not create — because that is what leaves the folder in a
    #: uniform, ready-to-run layout and what lets an interrupted run resume.
    #: The preview otherwise never mentions them: it is loose-TIF-only and
    #: non-recursive, so a user reading "2 well(s)" had no way to know four
    #: more folders would be zipped and have their TIFs deleted. This is that
    #: scope, measured with the same walk, so what the page promises and what
    #: the run does are the same list.
    existing_well_folders: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        """One line describing what this folder will become — or, when that is
        nothing, *which* of the three reasons applies.

        A single "No files matched the schema." covered all three, and two of
        them are not about the schema at all: a folder whose images are one
        level down (this tool reads only the top level) and a folder with no
        images in it. Both sent the user off to rebuild a schema that was never
        the problem.
        """
        if not self.examined:
            if self.nested_count:
                return (
                    "No images directly in this folder — "
                    f"{self.nested_count} found in subfolders, and this tool "
                    "reads only the top level."
                )
            return "No .tif/.tiff files in this folder."
        if not self.matched:
            return (
                f"None of the {self.examined} image(s) here match the schema — "
                "check the separator and the field positions."
            )
        parts = [f"{len(self.wells)} well(s)"]
        # Only report a dimension the schema actually names. Reporting "1 FOV(s)
        # × 0 timepoint(s)" for a schema holding neither invents one number and
        # states the other as a measurement.
        if "fov" in self.schema_fields:
            parts.append(f"{len(self.fovs)} FOV(s)")
        if "timepoint" in self.schema_fields:
            parts.append(f"{len(self.timepoints)} timepoint(s)")
        line = " × ".join(parts)
        if "channel" in self.schema_fields:
            line += f" · channels: {', '.join(self.channels) or '(none)'}"
        return line

    def existing_folder_note(self) -> str:
        """A sentence naming the well folders already in this folder that an
        in-place run will also zip, or ``""``.

        The in-place mode's scope is every well folder under the source, not
        only the wells it groups from the loose TIFs, and those folders have
        their TIFs deleted too. Nothing on the page said so, which made the
        preview an understatement of a destructive operation rather than a
        description of it.
        """
        if not self.existing_well_folders:
            return ""
        names = ", ".join(self.existing_well_folders[:6])
        more = "" if len(self.existing_well_folders) <= 6 else ", …"
        return (
            f"{len(self.existing_well_folders)} well folder(s) already in this "
            f"folder ({names}{more}) will also be zipped, and their TIFs "
            "deleted, by an in-place run."
        )


@dataclass(frozen=True)
class _WalkResult:
    by_well: dict           # well -> list[Path], matched + readable
    fovs: set
    timepoints: set
    channels: set
    examined: int
    unreadable_count: int
    unparsed_files: list


def _walk(
    search_dir: Path, *, schema: list[str], sep: str, recursive: bool,
) -> _WalkResult:
    """The one directory walk both :func:`scan_folder` and
    :func:`group_files_by_well` build on, so the preview and the executor
    can never disagree about what matched."""
    well_idx = _well_index(schema)
    fov_idx = _field_index(schema, "fov")
    tp_idx = _field_index(schema, "timepoint")
    channel_idx = _field_index(schema, "channel")

    by_well: dict[str, list[Path]] = {}
    fovs: set[str] = set()
    timepoints: set[str] = set()
    channels: set[str] = set()
    examined = 0
    unreadable = 0
    unparsed: list[str] = []

    for root, _dirs, files in os.walk(search_dir):
        for fname in files:
            if not fname.lower().endswith(_IMAGE_SUFFIXES):
                continue
            examined += 1
            stem = os.path.splitext(fname)[0]
            parts = stem.split(sep)
            well = None
            if well_idx is not None and well_idx < len(parts):
                well = canonical_well_label(parts[well_idx])
            if well is None:
                unparsed.append(os.path.join(root, fname))
                continue
            full = os.path.join(root, fname)
            if not os.path.isfile(full):
                unreadable += 1
                continue
            by_well.setdefault(well, []).append(Path(full))
            if fov_idx is not None and fov_idx < len(parts):
                fovs.add(parts[fov_idx])
            if tp_idx is not None and tp_idx < len(parts):
                timepoints.add(parts[tp_idx])
            if channel_idx is not None and channel_idx < len(parts):
                channels.add(parts[channel_idx])
        if not recursive:
            break

    return _WalkResult(
        by_well=by_well, fovs=fovs, timepoints=timepoints, channels=channels,
        examined=examined, unreadable_count=unreadable, unparsed_files=unparsed,
    )


def existing_well_folders(search_dir: Path) -> tuple[str, ...]:
    """Canonical wells of the top-level ``<well>/`` subfolders that already hold
    TIFs — the extra scope an in-place run takes on beyond the loose files.

    Deliberately the same shape :func:`datasets.import_loose.apply._zip_well_folders`
    walks (top level only, a directory whose name canonicalises to a well, at
    least one ``.tif``/``.tiff`` directly inside it), so the number the page
    shows is the number the run acts on rather than a second, nearby estimate.
    """
    wells: list[str] = []
    try:
        entries = sorted(Path(search_dir).iterdir())
    except OSError:
        return ()
    for entry in entries:
        if not entry.is_dir():
            continue
        well = canonical_well_label(entry.name)
        if not well:
            continue
        try:
            has_tifs = any(
                f.is_file() and f.suffix.lower() in _IMAGE_SUFFIXES
                for f in entry.iterdir()
            )
        except OSError:
            continue
        if has_tifs:
            wells.append(well)
    return tuple(wells)


#: Stop counting nested images once the answer is "plenty". The count exists to
#: say *which* problem this is, and any number past this reads the same.
_NESTED_PROBE_CAP = 500


def _count_images_below(search_dir: Path, cap: int = _NESTED_PROBE_CAP) -> int:
    """How many images sit below *search_dir*'s top level, up to *cap*."""
    found = 0
    for root, _dirs, files in os.walk(search_dir):
        if Path(root) == search_dir:
            continue                      # the top level is the level we know is empty
        for fname in files:
            if fname.lower().endswith(_IMAGE_SUFFIXES):
                found += 1
                if found >= cap:
                    return found
    return found


def scan_folder(
    search_dir: Path | str, *, filename_schema: str, filename_sep: str,
    recursive: bool = False,
) -> ImportPreview:
    """Scan *search_dir* for loose TIFs and report how they'd split into
    wells/FOVs/timepoints/channels, without writing anything.

    Non-recursive by default, matching ``well_plate_zipper``'s own default (a
    single ``os.walk`` level) — the in-place mode this preview describes only
    ever groups the folder it's pointed at, not its subfolders.
    """
    schema = parse_schema(filename_schema)
    result = _walk(Path(search_dir), schema=schema, sep=filename_sep, recursive=recursive)
    matched = sum(len(v) for v in result.by_well.values())
    # Only when the top level held nothing: look one level further and count, so
    # the page can say "they are in subfolders" instead of blaming the schema.
    # Bounded, and reached only in the already-empty case, so it costs nothing
    # on the path a real run takes.
    nested = 0
    if not result.examined and not recursive:
        nested = _count_images_below(Path(search_dir))
    return ImportPreview(
        wells=tuple(sorted(result.by_well)),
        fovs=tuple(sorted(result.fovs)),
        timepoints=tuple(sorted(result.timepoints)),
        channels=tuple(sorted(result.channels)),
        examined=result.examined,
        matched=matched,
        unreadable_count=result.unreadable_count,
        unparsed_files=tuple(result.unparsed_files),
        nested_count=nested,
        schema_fields=tuple(schema),
        existing_well_folders=existing_well_folders(Path(search_dir)),
    )


def group_files_by_well(
    search_dir: Path | str, *, filename_schema: str, filename_sep: str,
    recursive: bool = False,
) -> dict[str, list[Path]]:
    """Every matched file, grouped by canonical well — the input the
    new-destination executor writes per-well zips from."""
    schema = parse_schema(filename_schema)
    result = _walk(Path(search_dir), schema=schema, sep=filename_sep, recursive=recursive)
    return result.by_well


@dataclass(frozen=True)
class SchemaDetection:
    """What to do with a schema guess on this page, and what to say about it.

    Pure, so the rule can be tested without building the page. ``apply`` is
    whether the guess is good enough to write into the picker; ``message`` is
    the sentence shown beneath it; ``problem`` styles that sentence as a refusal
    rather than as a quieter success.
    """

    apply: bool
    message: str
    problem: bool

    #: Deliberately absent as a field. The Analyze host puts the guess's
    #: shortfall — which files will be skipped and why — in a second, louder
    #: register beneath the summary. This page must not: its own readout
    #: already ends with "N of M file(s) didn't match the schema and would be
    #: left alone (e.g. …)", two lines below, in the same size and colour. Two
    #: vocabularies for one count, stacked, is redundancy rather than emphasis
    #: — and the page's own line is the better written of the two, because it
    #: states the consequence rather than the mechanism.


def detection_for_grouping(guess) -> SchemaDetection:
    """Judge an inferred schema against what **this page** requires to run.

    The first version of this function reasoned from what grouping needs in
    principle — a well position and nothing else — and concluded that a
    single-channel plate, whose constant channel token means no channel slot
    can be placed, should be accepted here even though Analyze declines it.

    That was wrong, and wrong in the way :file:`CLAUDE.md` §7.3 #6 describes:
    it contradicted a comment six lines from the check that refutes it.
    :meth:`~datasets.import_loose.view.ImportLoosePage._can_run` blocks on
    ``SchemaPicker.errors()``, which validates against
    ``util.schema.PIPELINE_REQUIRED_FIELDS`` — ``("channel", "well")`` — and
    that comment already says why: *"a schema with no 'channel' produces
    archives Analyze cannot then read, which is the only reason to build
    them."* Applying a channel-less guess therefore turned a working page into
    a dead one on a single press, with Run and Queue dark and the explanation
    filed under success.

    So the test is both fields, the same pair the run requires — and the
    refusal says what is missing and what it costs, rather than declining
    silently.
    """
    if guess is None:
        return SchemaDetection(
            False,
            "No filename layout could be detected in that folder — the schema "
            "below is unchanged.",
            True,
        )
    fields = getattr(guess, "fields", [])
    missing = [f for f in ("well", "channel") if f not in fields]
    if not missing:
        return SchemaDetection(True, guess.summary(), False)
    if missing == ["channel"]:
        # Named separately because it is the case a reader will think should
        # have worked: the well is right there, and grouping by well is all
        # this page does. The archives are the part that cannot work.
        return SchemaDetection(
            False,
            f"{guess.summary()} No channel position was identified — every "
            f"sampled filename carries the same one, or none. The archives "
            f"this page writes are read by Analyze, which needs a channel, so "
            f"the schema below is unchanged: set the channel position by hand "
            f"if these files carry one.",
            True,
        )
    names = " or ".join(missing)
    return SchemaDetection(
        False,
        f"{guess.summary()} No {names} position was identified, so these files "
        f"cannot be grouped into archives Analyze can read and the schema "
        f"below is unchanged.",
        True,
    )
