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

from dataclasses import dataclass, field
from pathlib import Path

from util.dir_scan import files_in
from pipeline.czi_read import (
    CziLayout,
    CziReadError,
    ScenePeek,
    peek_scene_wells,
    read_czi_layout,
    wells_from_scenes,
)

#: The extension a CZI carries. A tuple for the same reason ``_OME_SUFFIXES``
#: is one: the membership test reads the same whether there is one or three.
CZI_SUFFIXES = (".czi",)


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
    #: *scene*, in the same order scenes appear in :attr:`positions`. Empty
    #: when the set does not name a distinct well for every scene.
    scene_wells: list[str] = field(default_factory=list)
    #: A problem that makes the set unconvertible, or ``""``.
    problem: str = ""

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

    def wells_from_files(self) -> list[str]:
        """One well per scene, in position order — or ``[]`` if unusable.

        All or nothing, and distinct, for the same reason a single file's
        :meth:`~pipeline.czi_read.CziLayout.wells_from_metadata` is: a mapping
        where some scenes land on their real wells and the rest land wherever
        the grid put them is right exactly where a user is most likely to
        spot-check it.
        """
        labels = list(self.scene_wells)
        if not labels or not all(labels) or len(set(labels)) != len(labels):
            return []
        return labels

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
        named = len(self.wells_from_files())
        source = (f", wells named by the file(s) ({named})" if named
                  else ", wells from the grid you set")
        channels = ", ".join(layout.channel_names) if layout.channel_names else "?"
        z_note = (f", {layout.size_z} Z-plane(s) max-projected"
                  if layout.size_z > 1 else "")
        return (
            f"{head}, {fov_text}, {layout.size_t} timepoint(s), "
            f"{layout.size_c} channel(s) ({channels}){z_note}, "
            f"{layout.width}×{layout.height} {layout.dtype}{source}."
        )


def discover_czi_files(source: Path | str) -> list[Path]:
    """Every ``.czi`` the selected path stands for, sorted by name.

    A file stands for itself and for nothing else — picking one file and
    silently converting its neighbours would be a surprise. A *folder* stands
    for the ``.czi`` files directly inside it, which is what a split-scene
    export is. The caller decides whether to widen a single file to its folder
    (the converter page offers that as a tick-box, because the same folder can
    hold one plate or several unrelated exports and only the user knows which).

    Sorted by name because that is the order ZEN writes a split export in and
    the order a grid mapping is laid over. Nothing recursive: a folder of
    folders is a different thing, and walking into one would silently sweep up
    a nested export.

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
                      key=lambda p: p.name)
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
#: other well has. Z is absent on purpose — it is projected away, so two files
#: whose stacks are different depths produce the same images, and
#: ``pipeline.ome_explode`` leaves Z out of its own ragged check for it.
_MUST_AGREE: tuple[tuple[str, str], ...] = (
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


def _wells_for(path: Path, layout: CziLayout) -> list[str]:
    """One well per scene in *path*, in scene order; ``""`` where unknown.

    Through :func:`~pipeline.czi_read.wells_from_scenes`, which the header-only
    peek below also goes through, so the plate a peek paints and the plate the
    run converts cannot be derived two different ways.
    """
    return wells_from_scenes(path, layout.scene_wells, layout.scenes)


def read_czi_set(source: Path | str, *, files: "list[Path] | None" = None) -> CziSet:
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

    for path in paths:
        result.layouts.append(read_czi_layout(path))

    first, first_path = result.layouts[0], paths[0]
    # Each file has to be sound in its own right before the set is compared:
    # "these two disagree about the channel names" is a confusing thing to say
    # about a file that holds no channels because it was truncated.
    for path, layout in zip(paths, result.layouts):
        unsound = layout.validation_report(path.name)
        if unsound:
            result.problem = unsound
            return result
    for path, layout in list(zip(paths, result.layouts))[1:]:
        clash = _disagreement(first, first_path, layout, path)
        if clash:
            result.problem = clash
            return result

    wells: list[str] = []
    for path, layout in zip(paths, result.layouts):
        wells.extend(_wells_for(path, layout))
        for scene in layout.scenes:
            for tile in layout.tiles.get(scene, [0]):
                result.positions.append(ScenePosition(path, scene, tile))
    result.scene_wells = wells
    return result


def read_czi_set_safely(source: Path | str, *,
                        files: "list[Path] | None" = None) -> CziSet:
    """:func:`read_czi_set`, with an unreadable member turned into a *problem*.

    For the page, which asks this on every keystroke and has one label to say
    the answer in. The exploder uses the raising form: by the time it runs, a
    file that cannot be read is a failure with a traceback worth keeping.
    """
    try:
        return read_czi_set(source, files=files)
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
               files: "list[Path] | None" = None) -> PlatePeek:
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
    seen: set[str] = set()
    for path in paths:
        try:
            scene = peek_scene_wells(path)
        except (CziReadError, OSError):
            # Not reported as a problem: the full read is the authority on a
            # file being broken, and saying so from here would put a refusal on
            # screen that a peek is not entitled to make.
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
