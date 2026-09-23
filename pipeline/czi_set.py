"""One *or more* CZI files that together hold one plate.

ZEN writes a plate scan two ways, and a converter that reads only the first is
a converter that reads half the exports people have:

* **One file, every scene.** The ordinary raw save. :mod:`pipeline.czi_read`
  reads it directly and this module is a set of one.
* **One file per scene.** ZEN's *Split Scenes* export, which is what a long
  acquisition is usually saved as — a folder of ``Exp7_A1.czi``,
  ``Exp7_A2.czi``, … each holding a single scene. Nothing inside any one of
  them says it is part of a plate; the plate is the folder.

A third shape exists and is deliberately *not* handled here: a single logical
CZI split into numbered **file parts** that share one subblock directory, which
ZEN produces for very large acquisitions. Those tiles carry a non-zero
``FilePart`` and their offsets are into a sibling file, so reading them through
one handle lands at an arbitrary position in the wrong file.
:meth:`~pipeline.czi_read.CziLayout.validation_report` refuses such a file by
name and says which two exports this converter does read.

**What a set has to agree about.** The files become one dataset, so their
channels, timepoints, pixel format and tile size must match — two files whose
channel lists differ would write images under names that mean different things
in different wells, which is the silent-substitution failure CLAUDE.md §7 is
about. Every mismatch is refused, naming both files.

Qt-free, numpy-free, and dependent only on :mod:`pipeline.czi_read` plus the
stdlib, so it stays inside the pipeline self-sufficiency contract
(ARCHITECTURE §6.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from util.aw_safe import swallowed
from util.dir_scan import files_in
from pipeline import scan_pool
from pipeline.explode_common import elapsed_hour_tokens
from pipeline.czi_read import (
    CziLayout,
    CziReadError,
    ScenePeek,
    cached_layout,
    cached_plate_metadata,
    peek_from_metadata,
    plate_metadata_for,
    read_czi_layout,
    remember_layout,
    remember_plate_metadata,
    wells_from_scenes,
)

#: The extension a CZI carries. A tuple for the same reason ``_OME_SUFFIXES``
#: is one: the membership test reads the same whether there is one or three.
CZI_SUFFIXES = (".czi",)

#: How many files a scan reads at once, at most.
#:
#: Deliberately **not** ``explode_common.pick_workers``, which caps at the CPU
#: count because the work it sizes is decoding and re-encoding pixels. Reading a
#: file's header or its subblock directory decodes nothing: it is a couple of
#: seeks and a few kilobytes, and on a share every one of them is a round trip
#: with the thread parked doing nothing. Sizing that to the cores would leave a
#: four-core laptop reading a 96-file plate four at a time for no reason.
#:
#: The cap is about the *share*, not the machine — enough requests in flight to
#: cover the latency, few enough not to hammer a filer or exhaust a mount's
#: connection pool. Eight is a common default for that and is what this is: a
#: defensible constant, **not** a measured optimum, because the only hardware it
#: could be measured against is the user's own.
_MAX_SCAN_WORKERS = 8

_T = TypeVar("_T")


@dataclass(frozen=True)
class ScenePosition:
    """One stage position: which file it is in, and where inside that file.

    The file travels with the coordinate rather than being held once per run,
    because a split-scene plate has one file per well and the exploder walks
    wells. Carrying it here is what lets one well's conversion open exactly the
    files that well needs and lets the single-file case stay a set of one with
    no branch of its own.
    """

    path: Path
    scene: int
    tile: int


@dataclass
class CziSet:
    """The files of one plate, their merged shape, and their stage positions."""

    files: list[Path] = field(default_factory=list)
    #: One entry per file, in ``files`` order.
    layouts: list[CziLayout] = field(default_factory=list)
    #: Every stage position, in acquisition order: file-major, then scene, then
    #: tile. File-major because a split-scene export visits one well per file.
    positions: list[ScenePosition] = field(default_factory=list)
    #: The well each position belongs to, where the files say — one entry per
    #: *scene*, in the same order scenes appear in :attr:`positions`. Repeats
    #: are ordinary: several scenes naming one well are that well's fields of
    #: view, which is what :meth:`scene_well_groups` reads them as. A scene the
    #: files name nothing for carries ``""``, and one of those anywhere sends
    #: the whole mapping to the grid.
    scene_wells: list[str] = field(default_factory=list)
    #: A problem that makes the set unconvertible, or ``""``.
    problem: str = ""
    #: Files of the selected folder that hold no image tiles — ZEN's Split
    #: Scenes export writes one master file describing the plate. Dropped
    #: from the set rather than refused, and kept here so the page can say
    #: which files it passed over: a converter that silently ignores an input
    #: the user pointed it at is indistinguishable from one that missed it.
    index_files: list = field(default_factory=list)
    #: What the files are short of the rectangle their axes imply, one
    #: sentence per file that is short of anything, or ``""``.
    #:
    #: Not a problem: a gap is skipped at conversion and the well is written
    #: without it. It is carried separately **because** it is not a problem —
    #: the page can say it before the run rather than after an hour of
    #: reading, which is what a refusal could only ever do at the end.
    shortfall_note: str = ""
    #: The timepoint token each image is named with — elapsed hours (``0h``,
    #: ``4h``, …) when the files' acquisition stamps yield a complete, distinct
    #: series, and the positional ``t0``, ``t1``, … otherwise.
    timepoint_tokens: list[str] = field(default_factory=list)
    #: What the user should be told about the timepoint names — a whole
    #: sentence, or ``""`` when there is nothing to say. Never empty when the
    #: tokens are positional *because* something was unreadable: a plate named
    #: by position with nothing said about it is indistinguishable from one
    #: whose writer recorded no times at all. Also carries the uneven-spacing
    #: notice, which is not a failure — the tokens are the measured times and
    #: only the tidying onto one interval was declined.
    timepoint_note: str = ""

    @property
    def layout(self) -> CziLayout:
        """The first file's layout, which every other file has been checked against.

        Safe to read as the plate's shape *only because* :func:`read_czi_set`
        refuses a set whose files disagree about anything the filenames depend
        on. Without that check this property would be the shape of whichever
        file happened to sort first.
        """
        return self.layouts[0] if self.layouts else CziLayout()

    @property
    def n_files(self) -> int:
        return len(self.files)

    @property
    def n_scenes(self) -> int:
        return sum(lay.n_scenes for lay in self.layouts)

    @property
    def n_positions(self) -> int:
        return len(self.positions)

    @property
    def fovs_per_scene(self) -> int:
        """Tiles per scene when every scene in every file agrees, else ``0``."""
        counts = {lay.fovs_per_scene for lay in self.layouts}
        return 0 if len(counts) != 1 or 0 in counts else counts.pop()

    def scene_well_groups(self) -> "list[tuple[str, list[int]]]":
        """The distinct wells the files name, each with its own scene indices.

        In first-appearance order, which for a plate scan is acquisition order,
        and inside each well the scene indices are in that order too — so the
        Nth scene of a well becomes its FOV N.

        **Several scenes may name one well, and that is the ordinary case for a
        multi-position plate scan.** ZEN writes one scene per imaged position
        and names each after the well it sits in, so a 96-well plate imaged at
        seven positions per well is 660-odd scenes naming ninety-six wells.
        Those scenes are the well's fields of view, which is what this grouping
        makes them. A repeat is not a revisit: CZI carries time on its own axis,
        so a well imaged again later in a run is another ``T``, never another
        scene. (``pipeline.nd2_explode`` groups over *runs* of equal names
        instead, because ND2 has no such guarantee and a returning stage really
        can mean a second visit.)

        **All or nothing**, for the same reason a single file's
        :meth:`~pipeline.czi_read.CziLayout.wells_from_metadata` is: a mapping
        where some scenes land on their real wells and the rest land wherever
        the grid put them is right exactly where a user is most likely to
        spot-check it. So an unnamed scene anywhere yields ``[]`` and the whole
        mapping falls to the grid.
        """
        labels = list(self.scene_wells)
        if not labels or not all(labels):
            return []
        groups: dict[str, list[int]] = {}
        for index, label in enumerate(labels):
            groups.setdefault(label, []).append(index)
        return list(groups.items())

    def wells_from_files(self) -> list[str]:
        """The distinct wells the files name, in acquisition order, or ``[]``.

        One entry per *well*, not per scene — see :meth:`scene_well_groups`.
        """
        return [well for well, _scenes in self.scene_well_groups()]

    def describe(self) -> str:
        """A one-line summary of the set, for the page readout and the log."""
        layout = self.layout
        if self.n_files > 1:
            head = (f"{self.n_files} file(s) holding {self.n_scenes} scene(s) "
                    f"between them")
        else:
            head = f"{self.n_scenes} scene(s)"
        fovs = self.fovs_per_scene
        fov_text = f"{fovs} tile(s) each" if fovs else "a varying number of tiles"
        named = self.wells_from_files()
        if not named:
            source = ", wells from the grid you set"
        elif len(named) < self.n_scenes:
            # Both numbers, because "96 wells" over a 660-scene export reads
            # exactly like a plate that only ever held ninety-six scenes.
            source = (f", wells named by the file(s) ({len(named)}, holding "
                      f"the {self.n_scenes} scene(s) between them)")
        else:
            source = f", wells named by the file(s) ({len(named)})"
        channels = ", ".join(layout.channel_names) if layout.channel_names else "?"
        z_note = (f", {layout.size_z} Z-plane(s) max-projected"
                  if layout.size_z > 1 else "")
        return (
            f"{head}, {fov_text}, {layout.size_t} timepoint(s), "
            f"{layout.size_c} channel(s) ({channels}){z_note}, "
            f"{layout.width}×{layout.height} {layout.dtype}{source}."
        )


#: Splits a filename into text and number runs, so ``s2`` sorts before ``s10``.
_DIGITS = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    """A sort key that reads runs of digits as numbers.

    Plain lexicographic order puts ``scan_s10`` between ``scan_s1`` and
    ``scan_s2``, and for a split export the file order **is** the acquisition
    order a grid mapping lays its well labels over — so on a 3x4 grid that put
    scene 2 in B01 and scene 10 in A02, with the run reporting success and the
    plate wrong in a way only someone who knew the scan could spot. ZEN does not
    zero-pad its scene numbers, and every test here happened to use labels that
    did.

    Digits are ASCII-only — Python's ``\\d`` is Unicode by default and would
    read a fullwidth digit as a number — and case is folded on the text runs so
    ``Exp_a2`` and ``Exp_A10`` order together.
    """
    parts = _DIGITS.split(name)
    return tuple(int(part) if part.isdigit() and part.isascii()
                 else part.lower()
                 for part in parts)


def scan_workers(n_files: int) -> int:
    """How many files to read at once for a scan of *n_files*."""
    return max(1, min(_MAX_SCAN_WORKERS, n_files))


def _per_file(fn: "Callable[[Any], _T]", items: list, report=None, *,
              cached: "Callable[[Any], Any] | None" = None,
              remember: "Callable[[Any, Any], None] | None" = None) -> list:
    """Run *fn* over *items* concurrently, **in order**.

    The order is load-bearing and not a nicety: it is the order ZEN wrote a
    split export in, the order a grid mapping lays its well labels over, and the
    order ``CziSet.positions`` is walked in. Collecting results as they finish
    would make a plate's well mapping depend on which file the filer answered
    first. :func:`pipeline.scan_pool.map_ordered` yields by argument order
    however the work completes, which is exactly that guarantee — and it
    re-raises each call's exception at its own position, so a set whose third
    file is broken still fails on the third file.

    Across **processes** rather than threads once the plate is big enough to
    pay for starting them, because this module's work is a Python parser and
    threads of it contend for the interpreter lock rather than overlapping —
    ``scan_pool`` states the measurements. *fn* is therefore a module-level
    function and never a closure: a spawned child is told a module and a name.

    *cached* and *remember* keep the parent's answer from being recomputed in
    a child that cannot see it. A worker process has its own memory, so the
    stat-keyed caches in :mod:`pipeline.czi_read` are populated in the child
    and die with it — without these, moving the scan off-thread would quietly
    undo the reuse across the four passes a plate is read in. Both run in the
    **parent** and may be closures; only *fn* crosses the process boundary.
    """
    total = len(items)
    #: *report* hears one tick per file, whether the file was read or already
    #: known: the count is what the user is waiting on, and a plate that
    #: appears to stall through its cached half would be a worse lie than no
    #: count at all.
    seen = 0

    def _tick() -> None:
        nonlocal seen
        if report is not None:
            report.progress(seen, total)
        seen += 1

    workers = scan_workers(total)
    if cached is None:
        return scan_pool.map_ordered(fn, list(items), workers=workers,
                                     on_result=lambda _index: _tick())

    known = [cached(item) for item in items]
    pending = [item for item, hit in zip(items, known) if hit is None]
    for hit in known:
        if hit is not None:
            _tick()
    fresh = scan_pool.map_ordered(fn, pending, workers=scan_workers(len(pending)),
                                  on_result=lambda _index: _tick())
    supply = iter(fresh)
    out = []
    for item, hit in zip(items, known):
        if hit is not None:
            out.append(hit)
            continue
        result = next(supply)
        if remember is not None:
            remember(item, result)
        out.append(result)
    return out


def _read_one_layout(item: tuple) -> CziLayout:
    """One file's layout, for a worker that may be a separate process.

    Module-level, and taking its arguments as one picklable tuple, because
    that is how a spawned child is told what to run.

    The third element is the plate document the **parent** already parsed, if
    it has one. A worker process cannot see the parent's caches, so without
    this a split export's full read would re-read and re-parse the whole
    plate's document once per file — the cost the caching was added to remove.
    Sending it is kilobytes; re-reading it is megabytes over a share.
    """
    path, read_times, metadata = item
    return read_czi_layout(path, read_times=read_times, metadata=metadata)


def _peek_one_file(item: "tuple[Path, bool]") -> tuple:
    """``(peek, document)`` for one file, or ``(None, None)`` if unreadable.

    The parsed document comes back with the answer so the **parent** can
    remember it. In a worker process the module-level caches are the worker's
    and die with it, so a peek that returned only its conclusion would leave
    the parent to read and parse every file's document a second time for the
    full read that follows.

    The failure is caught **inside** the worker rather than around the whole
    batch, so one unreadable file costs that one file and not the rest of the
    plate. Not reported as a problem either: the full read is the authority on
    a file being broken, and saying so from here would put a refusal on screen
    that a peek is not entitled to make.
    """
    path, from_filename = item
    try:
        meta = plate_metadata_for(path)
        return peek_from_metadata(path, meta, from_filename=from_filename), meta
    except (CziReadError, OSError):
        swallowed(f"peek {path.name}")
        return None, None


def _peek_from_cache(path: Path, from_filename: bool) -> "tuple | None":
    """The peek the parent can answer without reading, or ``None``."""
    meta = cached_plate_metadata(path)
    if meta is None:
        return None
    return peek_from_metadata(path, meta, from_filename=from_filename), meta


def _remember_peeked(item: "tuple[Path, bool]", answer: tuple) -> None:
    """Keep what a worker parsed, so the full read need not parse it again."""
    _peek, meta = answer
    if meta is not None:
        remember_plate_metadata(item[0], meta)


def discover_czi_files(source: Path | str) -> list[Path]:
    """Every ``.czi`` the selected path stands for, sorted by name.

    A file stands for itself and for nothing else — picking one file and
    silently converting its neighbours would be a surprise. A *folder* stands
    for the ``.czi`` files directly inside it, which is what a split-scene
    export is. The caller decides whether to widen a single file to its folder
    (the converter page offers that as a tick-box, because the same folder can
    hold one plate or several unrelated exports and only the user knows which).

    Sorted by :func:`natural_key`, not lexicographically, because that is the
    order ZEN writes a split export in and the order a grid mapping is laid
    over — and ZEN does not zero-pad its scene numbers. Nothing recursive: a
    folder of folders is a different thing, and walking into one would silently
    sweep up a nested export.

    Through :func:`util.dir_scan.files_in` rather than ``iterdir()`` plus
    ``is_file()``. That pair is one ``stat`` per entry, which on Windows is a
    file handle opened and closed, which against a network share is a round
    trip each — a 96-file plate on a NAS being exactly the case this function
    exists for. ``scandir`` answers the same question from the listing the
    enumeration already paid for.
    """
    source = Path(source)
    if source.is_file():
        return [source] if source.suffix.lower() in CZI_SUFFIXES else []
    if source.is_dir():
        return sorted((p for p in files_in(source)
                       if p.suffix.lower() in CZI_SUFFIXES),
                      key=lambda p: natural_key(p.name))
    return []


def siblings_of(source: Path | str) -> list[Path]:
    """Every ``.czi`` in *source*'s own folder, *source* included, sorted."""
    source = Path(source)
    folder = source if source.is_dir() else source.parent
    return discover_czi_files(folder)


#: What two files of one plate have to agree about, and the noun to use when
#: they do not. Each is a property the output *filenames* or their meaning
#: depend on: two files with different channel lists write images whose channel
#: token means one thing in one well and another thing in the next, and two
#: with different timepoint counts leave a well short of a timepoint that every
#: other well has.
#:
#: ``fovs_per_scene`` is never 0 by the time it is compared, so this needs no
#: companion "is it knowable?" check: a one-scene file has exactly one tile
#: count, and a multi-scene file whose scenes disagree is refused by
#: ``validation_report`` on the line above. One was written here anyway and was
#: unreachable — the branch class §7.7 is about — and a mutation of it left the
#: whole suite green, which is how it was found.
#:
#: ``fovs_per_scene`` is here because a set is chopped into wells by a *count*:
#: ``resolve_well_map``'s grid arm divides the flat position list evenly, so
#: files holding 1, 2 and 1 tiles put the second file's two tiles either side
#: of a well boundary — one well ends up with tiles from two different files and
#: the run reports success. ``CziLayout._ragged_scenes`` catches this inside one
#: file and is blind to it across a set, because a split member has a single
#: scene and there is nothing for it to compare.
#:
#: Z is absent on purpose — it is projected away, so two files whose stacks are
#: different depths produce the same images, and ``pipeline.ome_explode`` leaves
#: Z out of its own ragged check for it.
_MUST_AGREE: tuple[tuple[str, str], ...] = (
    ("fovs_per_scene", "number of tiles per scene"),
    ("size_c", "number of channels"),
    ("size_t", "number of timepoints"),
    ("channel_names", "channel names"),
    ("dtype", "pixel format"),
    ("width", "image width"),
    ("height", "image height"),
)


def _disagreement(first: CziLayout, first_path: Path,
                  other: CziLayout, other_path: Path) -> str:
    """The first property these two files disagree about, or ``""``."""
    for attribute, noun in _MUST_AGREE:
        want = getattr(first, attribute)
        got = getattr(other, attribute)
        if want != got:
            return (
                f"{first_path.name} and {other_path.name} disagree about the "
                f"{noun}: {want!r} against {got!r}. Files converted together "
                f"become one dataset, so a difference here would mean the same "
                f"filename means different things in different wells. Convert "
                f"them separately, or re-export them with matching settings."
            )
    return ""


def _wells_for(path: Path, layout: CziLayout, *,
               from_filename: bool) -> list[str]:
    """One well per scene in *path*, in scene order; ``""`` where unknown.

    Through :func:`~pipeline.czi_read.wells_from_scenes`, which the header-only
    peek below also goes through, so the plate a peek paints and the plate the
    run converts cannot be derived two different ways.

    *from_filename* is False for a **lone** file. The filename rule exists for
    ZEN's split export, where each member is named after the scene it holds;
    applied to a single file it reads any well-shaped fragment of any name as a
    well, so ``20240612_Plate_D1_60x.czi`` — one scene, nine tiles, no scene
    metadata — became well D01 where it used to be refused and pointed at the
    grid. ``D1`` there is far likelier to be a condition than a plate position,
    and a single file gives the rule nothing to corroborate it with.
    """
    return wells_from_scenes(path, layout.scene_wells, layout.scenes,
                             from_filename=from_filename)


def read_czi_set(source: Path | str, *, files: "list[Path] | None" = None,
                 report=None) -> CziSet:
    """Read *source* — a file, or a folder of them — as one plate.

    *files* names the exact set to read, for a caller that has already decided
    (the converter page, whose tick-box widens one file to its folder). Without
    it, :func:`discover_czi_files` decides: a file is a set of one, a folder is
    every ``.czi`` directly in it.

    Never raises for a bad member. A set whose files cannot be read together is
    returned with :attr:`CziSet.problem` set, because the page shows this on
    every keystroke and an exception there is a traceback in a readout. A
    member that cannot be read *at all* raises
    :class:`~pipeline.czi_read.CziReadError` from ``read_czi_layout``, which is
    the caller's to catch — that is a broken file rather than a mismatched set.
    """
    paths = list(files) if files is not None else discover_czi_files(source)
    result = CziSet(files=paths)
    if not paths:
        result.problem = (
            f"No .czi files in {Path(source).name or source}."
        )
        return result

    # Read concurrently: each file is its own handle and its own layout, and
    # the cost is round trips rather than cycles.
    #
    # Only the first file is asked for its acquisition stamps. Every member of
    # a split-scene export covers the same timepoints, so the others would pay
    # a round trip per timepoint each to re-derive one answer — and the first
    # file is the right one to take it from for a second reason: the files are
    # in acquisition order, so it holds the well the stage visited first, which
    # is the position whose clock names the run (CLAUDE.md, ARCHITECTURE
    # §6.2a).
    first_path_for_times = paths[0]

    # The plate document rides with each job when the parent already has it,
    # because a worker process cannot reach back for it — see
    # ``_read_one_layout``. A peek normally ran first and left them all here.
    jobs = [(path, path == first_path_for_times, cached_plate_metadata(path))
            for path in paths]
    result.layouts = _per_file(
        _read_one_layout, jobs, report,
        cached=lambda item: cached_layout(item[0], item[1]),
        remember=lambda item, layout: remember_layout(item[0], item[1], layout))

    # Each file has to be sound in its own right before the set is compared:
    # "these two disagree about the channel names" is a confusing thing to say
    # about a file that holds no channels because it was truncated.
    # ZEN's Split Scenes export writes a **master** file beside the per-scene
    # ones: it describes the plate and holds no image tiles of its own. It is
    # part of the export rather than a fault in it, so it is dropped from the
    # set instead of refused — refusing it took the whole folder down, which
    # is the one thing a file holding no images should not be able to do.
    #
    # Dropped after the read because only the read can tell: "holds no image
    # tiles" is a property of the subblock directory, not of the name. It
    # sorts first on every export seen so far (``Exp.czi`` before
    # ``Exp_A01.czi``), which is why it also had to stop being the file the
    # timing is taken from and the file every other member is compared
    # against.
    with_images = [(path, layout) for path, layout
                   in zip(paths, result.layouts) if layout.n_blocks]
    if with_images and len(with_images) < len(paths):
        result.index_files = [path for path, layout
                              in zip(paths, result.layouts)
                              if not layout.n_blocks]
        paths = [path for path, _ in with_images]
        result.files = paths
        result.layouts = [layout for _, layout in with_images]
        # The acquisition stamps were asked of whichever file sorted first,
        # and that was the one just dropped. A layout read without them
        # carries ``None`` rather than an empty scan, which is what tells the
        # two apart — so this re-reads exactly when the timing went with the
        # master, and never otherwise.
        if result.layouts[0].time_scan is None:
            result.layouts[0] = read_czi_layout(paths[0], read_times=True)

    # Bound after the drop, never before it. The master sorts first, so a
    # ``first`` taken ahead of this made it the baseline every other member
    # was compared against — and the comparison it then failed was "0 tiles
    # against 1", a second refusal from the same file wearing a different
    # sentence.
    first, first_path = result.layouts[0], paths[0]

    notes: list[str] = []
    if result.index_files:
        # Said, not silently done. A converter that passes over a file the
        # user pointed it at, without a word, is indistinguishable from one
        # that failed to notice it — and this one is dropping a file whose
        # name is on the export.
        shown = ", ".join(path.name for path in result.index_files[:2])
        more = len(result.index_files) - min(2, len(result.index_files))
        notes.append(
            f"{len(result.index_files)} file(s) hold no image tiles and are "
            f"not converted ({shown}{', …' if more else ''}) — ZEN writes one "
            f"such file per split-scene export to describe the plate.")
    for path, layout in zip(paths, result.layouts):
        unsound = layout.validation_report(path.name)
        if unsound:
            result.problem = unsound
            return result
        short = layout.shortfall_note(path.name)
        if short:
            notes.append(short)
    #: Capped, because a plate whose export was interrupted can have every
    #: file short and a readout is not a log. The count that follows is the
    #: honest part: a list that silently stopped at three would say the plate
    #: has three bad files when it has six hundred.
    if notes:
        shown = notes[:3]
        more = len(notes) - len(shown)
        result.shortfall_note = " ".join(shown) + (
            f" {more} further file(s) are short too." if more else "")
    for path, layout in list(zip(paths, result.layouts))[1:]:
        clash = _disagreement(first, first_path, layout, path)
        if clash:
            result.problem = clash
            return result

    wells: list[str] = []
    from_filename = len(paths) > 1
    for path, layout in zip(paths, result.layouts):
        wells.extend(_wells_for(path, layout, from_filename=from_filename))
        for scene in layout.scenes:
            for tile in layout.tiles.get(scene, [0]):
                result.positions.append(ScenePosition(path, scene, tile))
    result.scene_wells = wells
    result.timepoint_tokens, result.timepoint_note = _timepoint_tokens(first)
    return result


def _timepoint_tokens(layout: CziLayout) -> tuple[list[str], str]:
    """``(tokens, note)`` — elapsed hours where the file supports them.

    Every route to the positional fallback is a *degradation*: the acquisition
    recorded when each plane was taken and the archives will not carry it, so
    Review can order the timepoints and put no number on an axis. Each one
    therefore comes back with a note the run log prints, because a plate named
    ``t0``/``t1`` because a tag could not be read looks exactly like one whose
    writer recorded no times at all.

    The rendering and the completeness, collision and parseability tests are
    :func:`pipeline.explode_common.elapsed_hour_tokens`, shared with the
    OME-TIFF converter so that one acquisition exported both ways cannot end up
    with two spellings of the same elapsed hour.
    """
    positional = list(layout.timepoint_tokens)
    scan = layout.time_scan
    if scan is None:
        return positional, ""
    resolved = elapsed_hour_tokens(scan.hours_by_index, layout.size_t,
                                   source_reasons=scan.reasons)
    if resolved.tokens is not None:
        return resolved.tokens, (f"{resolved.note[:1].upper()}{resolved.note[1:]}."
                                 if resolved.note else "")
    if not resolved.reasons:
        return positional, ""
    return positional, (
        f"Timepoints are named by position (t0, t1, …) rather than by elapsed "
        f"time — {'; '.join(resolved.reasons)}.")


def read_czi_set_safely(source: Path | str, *,
                        files: "list[Path] | None" = None,
                        report=None) -> CziSet:
    """:func:`read_czi_set`, with an unreadable member turned into a *problem*.

    For the page, which asks this on every keystroke and has one label to say
    the answer in. The exploder uses the raising form: by the time it runs, a
    file that cannot be read is a failure with a traceback worth keeping.
    """
    try:
        return read_czi_set(source, files=files, report=report)
    except CziReadError as exc:
        return CziSet(files=list(files or ()), problem=str(exc))


# ── peeking, for a plate that has not been read yet ─────────────────────────

@dataclass
class PlatePeek:
    """What a plate's files claim about their wells, read from headers alone.

    The point of it is a plate map drawn *before* the plate has been read. A
    full :func:`read_czi_set` walks every file's subblock directory — about a
    megabyte for a 96-well plate, whatever the images weigh — and on a network
    share that is the difference between a plate map appearing at once and one
    appearing after every file has been round-tripped. A peek reads the
    112-byte file header and the metadata segment it points at: a few hundred
    bytes to a few kilobytes per file, and no pixels either way.

    It is **provisional**, and the page that draws it has to say so. A peek
    cannot know whether a file is sound, whether the files agree with each
    other, or how many tiles a scene holds — all of which can still refuse the
    conversion. :attr:`complete` is False when some file carries no readable
    metadata, because the filename may only stand in for a scene name on a file
    holding exactly one scene and a peek cannot count scenes without metadata.
    """

    files: list[Path] = field(default_factory=list)
    peeks: list[ScenePeek] = field(default_factory=list)
    #: Every well the headers named, de-duplicated, in the order first seen.
    wells: list[str] = field(default_factory=list)
    #: Files whose headers named nothing, so the plate below is short of them.
    unnamed: list[Path] = field(default_factory=list)
    bytes_read: int = 0
    problem: str = ""

    @property
    def complete(self) -> bool:
        """True when every file named a well, so the plate is the whole plate."""
        return bool(self.files) and not self.unnamed and not self.problem

    def note(self) -> str:
        """One line for the page, or ``""`` when there is nothing to qualify."""
        if self.problem:
            return self.problem
        if not self.wells:
            return ""
        read = (f"{self.bytes_read / 1024:.0f} KB" if self.bytes_read >= 1024
                else f"{self.bytes_read} bytes")
        head = (f"{len(self.wells)} well(s) named in the file headers "
                f"({read} read)")
        if self.unnamed:
            return (f"{head}; {len(self.unnamed)} file(s) name none, so the "
                    f"plate is not the whole plate until they are read.")
        return f"{head} — not yet checked."


def peek_wells(source: Path | str, *,
               files: "list[Path] | None" = None, report=None) -> PlatePeek:
    """Read the wells *source*'s files claim, from their headers alone.

    Never raises: a file that cannot be peeked is simply one the plate does not
    know about yet, and this is called to draw something quickly rather than to
    decide anything. :func:`read_czi_set` is what a caller asks when it needs
    an answer it can convert against.
    """
    paths = list(files) if files is not None else discover_czi_files(source)
    peek = PlatePeek(files=paths)
    if not paths:
        peek.problem = f"No .czi files in {Path(source).name or source}."
        return peek
    from_filename = len(paths) > 1

    peek_jobs = [(path, from_filename) for path in paths]
    # A peek has no cache hook of its own: the parsed document it wants is
    # remembered under the file, and asking for it is how it gets remembered.
    # What a hit would save is the read, which ``peek_scene_wells`` skips by
    # itself when the parent already holds the document — and in a worker
    # process there is nothing to hold.
    peeked = _per_file(
        _peek_one_file, peek_jobs, report,
        cached=lambda item: _peek_from_cache(item[0], item[1]),
        remember=_remember_peeked)
    seen: set[str] = set()
    for path, (scene, _document) in zip(paths, peeked):
        if scene is None:
            peek.unnamed.append(path)
            continue
        peek.peeks.append(scene)
        peek.bytes_read += scene.bytes_read
        named = [w for w in scene.wells if w]
        if not named:
            peek.unnamed.append(path)
        for well in named:
            if well not in seen:
                seen.add(well)
                peek.wells.append(well)
    return peek
