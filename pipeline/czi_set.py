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

from pipeline.czi_read import (
    CziLayout,
    CziReadError,
    read_czi_layout,
    well_from_scene_name,
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
    """
    source = Path(source)
    if source.is_file():
        return [source] if source.suffix.lower() in CZI_SUFFIXES else []
    if source.is_dir():
        return sorted((p for p in source.iterdir()
                       if p.is_file() and p.suffix.lower() in CZI_SUFFIXES),
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

    The scene's own metadata first, because that is the acquisition saying
    which well it scanned. Failing that, and **only for a file holding exactly
    one scene**, the filename: ZEN's split-scene export names each file after
    the scene it holds (``Exp7_A1.czi``), and a file that *is* a scene may
    legitimately be identified by its name. The one-scene condition is what
    keeps that from misreading a whole-plate file that happens to be called
    ``Exp_A1.czi`` as a single well of ninety-six fields.
    """
    labels = [layout.scene_wells.get(scene, "") for scene in layout.scenes]
    if len(labels) == 1 and not labels[0]:
        labels = [well_from_scene_name(path.stem)]
    return labels


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
