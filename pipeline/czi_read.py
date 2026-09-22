"""Read a Zeiss CZI (ZISRAW) file: what it holds, and one 2-D plane at a time.

A CZI is a flat sequence of length-prefixed *segments*. Four of them matter
here: the file header (which points at the other two), the subblock directory
(one entry per stored tile, with that tile's position on every dimension), the
metadata segment (one XML document describing channels, scenes and sizes), and
the subblock segments themselves (each holding the pixels of exactly one 2-D
tile). There is no image array to read — the file is a bag of tiles, and which
tile is which is entirely a question of the coordinates in its directory entry.

The coordinates this module cares about, in the order the exploder walks them:

* ``S`` — **scene**. On a plate scan one scene is one *well*, and the metadata
  usually names it (``<Scene Name="A1">``), which is why this importer can map
  wells without being told the grid. A file with no ``S`` axis has one scene.
* ``M`` — **mosaic tile**. Several tiles within one scene are the fields of view
  imaged in that well. They are kept as separate FOVs and never stitched:
  stitching is a decision about overlap and blending that belongs to the
  acquisition software, and a silently stitched image is not the image the
  microscope took.
* ``T`` / ``C`` / ``Z`` — timepoint, channel, focal plane, as usual.

Anything else a file declares (``R`` rotation, ``I`` illumination, ``H`` phase,
``V`` view, ``B`` legacy block) is reported by :meth:`CziLayout.validation_report`
rather than quietly collapsed, because every one of them is an axis whose planes
would otherwise be written over each other under one filename.

**Pyramid subblocks are discarded.** A CZI written for viewing carries
lower-resolution copies of each tile alongside the real one, distinguished by a
``StoredSize`` smaller than the ``Size`` of the same dimension (and by a
non-zero ``PyramidType``). They are the same pixels at the wrong scale; reading
one as if it were the acquisition would produce a correctly named image of the
wrong thing.

Depends on the stdlib plus numpy only, with :mod:`imagecodecs` imported lazily
and *only* for a file whose tiles are compressed — so the module stays inside
the pipeline self-sufficiency contract (ARCHITECTURE §6.2) and a test suite
without imagecodecs can still exercise every uncompressed path.
"""

from __future__ import annotations

import copy
import os
import re
import struct
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

import numpy as np

from pipeline.explode_common import TimeScan  # noqa: F401 (re-export)
from util.filename_token import safe_token
from util.well_token import canonical_well_label

# ── ZISRAW on-disk structures ────────────────────────────────────────────────
#
# All little-endian, all unaligned — hence the "<" prefix on every Struct, which
# also fixes each one's size at the sum of its fields rather than whatever the
# host would pad it to.

#: Every segment opens with this: a 16-byte ASCII id, then the bytes reserved
#: for it and the bytes actually used.
_SEGMENT_HEADER = struct.Struct("<16sqq")

#: ``ZISRAWFILE``: version, two GUIDs, and the offsets of the directory, the
#: metadata and the attachment directory.
_FILE_HEADER = struct.Struct("<iiii16s16siqqiq")

#: ``DirectoryEntryDV``: the fixed part of a subblock's coordinates. The
#: variable part is ``dimension_count`` × :data:`_DIMENSION_ENTRY`.
_DIRECTORY_ENTRY = struct.Struct("<2siqiiBB4si")

#: One axis of one subblock: which axis, where it starts, how big it is, and
#: how much of that was actually stored (smaller ⇒ a downsampled pyramid copy).
_DIMENSION_ENTRY = struct.Struct("<4siifi")

#: ``ZISRAWDIRECTORY``: an entry count, then that many directory entries.
_DIRECTORY_HEADER_LEN = 128

#: ``ZISRAWMETADATA``: XML size, attachment size, then 248 spare bytes.
_METADATA_HEADER_LEN = 256

#: A subblock segment's fixed prologue, before its (variable) directory entry.
_SUBBLOCK_PROLOGUE = struct.Struct("<iiq")

#: The prologue plus the directory entry are padded out to at least this much,
#: so a subblock's metadata never begins before offset 256 of its segment data.
_SUBBLOCK_MIN_HEADER = 256

#: CZI ``PixelType`` → numpy dtype, for the monochrome types a fluorescence
#: acquisition produces. The colour types (Bgr24/48/96, Bgra32) and the complex
#: ones are deliberately absent: they are real CZI pixel types, but an image
#: with three interleaved colour samples is not a channel this pipeline can
#: quantify, and picking one of its samples would be an unannounced choice
#: about which. :meth:`CziLayout.validation_report` names them instead.
PIXEL_DTYPES: dict[int, str] = {
    0: "uint8",      # Gray8
    1: "uint16",     # Gray16
    2: "float32",    # Gray32Float
    12: "int32",     # Gray32
    13: "float64",   # Gray64
}

#: The colour / complex pixel types, by name, so a refusal can say which one the
#: file carries rather than only that the number is unknown.
_UNSUPPORTED_PIXEL_TYPES: dict[int, str] = {
    3: "Bgr24", 4: "Bgr48", 8: "Bgr96Float", 9: "Bgra32",
    10: "Gray64ComplexFloat", 11: "Bgr192ComplexFloat",
}

#: CZI ``Compression`` → the name to use when talking about it.
COMPRESSION_NAMES: dict[int, str] = {
    0: "uncompressed", 1: "JPEG", 2: "LZW", 4: "JPEG-XR",
    5: "Zstandard", 6: "Zstandard (packed)",
}

#: The axes this module resolves. Everything else a file declares is refused
#: rather than collapsed — see the module docstring.
_KNOWN_AXES = frozenset({"X", "Y", "Z", "C", "T", "S", "M"})

#: Axes that may appear with a size of 1 without meaning anything. A file that
#: declares one of these with a real extent holds planes this importer would
#: write over each other, which is what ``validation_report`` refuses.
_IGNORABLE_AXES = frozenset({"B", "R", "I", "H", "V"})


class CziReadError(Exception):
    """A user-facing reason a CZI could not be read."""


def _token(raw: bytes) -> str:
    """The ASCII head of a fixed-width, NUL-padded field."""
    return raw.split(b"\0", 1)[0].decode("ascii", "replace").strip()


@dataclass(frozen=True)
class SubBlock:
    """One stored 2-D tile: where it is in the file, and what it is a tile *of*."""

    file_position: int
    pixel_type: int
    compression: int
    pyramid_type: int
    #: axis → ``(start, size, stored_size)``, exactly as the directory states it.
    dims: dict[str, tuple[int, int, int]]
    #: Which file of a multi-file CZI holds this tile's pixels. ``0`` is the
    #: file the directory is in; anything else is a *sibling* file, and
    #: ``file_position`` is then an offset into that one rather than this one.
    file_part: int = 0

    def start(self, axis: str, default: int = 0) -> int:
        entry = self.dims.get(axis)
        return default if entry is None else entry[0]

    def size(self, axis: str, default: int = 1) -> int:
        entry = self.dims.get(axis)
        return default if entry is None else entry[1]

    @property
    def is_pyramid(self) -> bool:
        """True for a downsampled copy rather than an acquired tile.

        Two independent tells, and either one is enough. ``PyramidType`` is the
        field that exists to say so; ``StoredSize < Size`` on X or Y is what a
        downsampled tile *is*, and some writers leave the type at zero while
        still emitting the reduced copies.
        """
        if self.pyramid_type:
            return True
        return any(self.dims.get(ax, (0, 1, 1))[2] < self.dims.get(ax, (0, 1, 1))[1]
                   for ax in ("X", "Y"))


def _read_exactly(fh: BinaryIO, n: int, what: str) -> bytes:
    """*n* bytes, or a :class:`CziReadError` naming what ran out."""
    data = fh.read(n)
    if len(data) != n:
        raise CziReadError(
            f"The file ends in the middle of its {what} ({len(data)} of {n} "
            f"bytes). It is truncated — copy it again from the original."
        )
    return data


def _parse_directory_entry(data: bytes, offset: int) -> tuple[SubBlock, int]:
    """One ``DirectoryEntryDV`` at *offset*, and the offset just past it."""
    if offset + _DIRECTORY_ENTRY.size > len(data):
        raise CziReadError("The subblock directory ends mid-entry; the file is "
                           "truncated.")
    (schema, pixel_type, file_position, file_part, compression, pyramid_type,
     _r1, _r2, n_dims) = _DIRECTORY_ENTRY.unpack_from(data, offset)
    if _token(schema) != "DV":
        raise CziReadError(
            f"Unknown subblock directory entry type {_token(schema)!r}; this "
            f"reader understands the 'DV' layout every CZI writer emits."
        )
    if not 0 <= n_dims <= 64:
        raise CziReadError(
            f"A subblock claims {n_dims} dimensions, which is not a number a "
            f"CZI can hold — the file is damaged."
        )
    pos = offset + _DIRECTORY_ENTRY.size
    dims: dict[str, tuple[int, int, int]] = {}
    for _ in range(n_dims):
        if pos + _DIMENSION_ENTRY.size > len(data):
            raise CziReadError("The subblock directory ends mid-entry; the file "
                               "is truncated.")
        axis, start, size, _coord, stored = _DIMENSION_ENTRY.unpack_from(data, pos)
        dims[_token(axis).upper()] = (start, size, stored)
        pos += _DIMENSION_ENTRY.size
    block = SubBlock(file_position=file_position, pixel_type=pixel_type,
                     compression=compression, pyramid_type=pyramid_type,
                     file_part=file_part, dims=dims)
    return block, pos


def _read_file_header(fh: BinaryIO) -> tuple[int, int]:
    """``(directory_position, metadata_position)`` from the opening segment."""
    fh.seek(0)
    sid, _allocated, _used = _SEGMENT_HEADER.unpack(
        _read_exactly(fh, _SEGMENT_HEADER.size, "file header"))
    if _token(sid) != "ZISRAWFILE":
        raise CziReadError(
            "This is not a CZI file: it does not begin with a ZISRAWFILE "
            "segment. Check that the file is a Zeiss .czi and not, say, an "
            "exported TIFF that was renamed."
        )
    (_major, _minor, _r1, _r2, _primary_guid, _guid, _file_part,
     directory_position, metadata_position, _update_pending,
     _attachment_dir) = _FILE_HEADER.unpack(
        _read_exactly(fh, _FILE_HEADER.size, "file header"))
    # Unpacked by name rather than by index. Reading the two offsets as
    # ``fields[8]`` and ``fields[9]`` was off by one — it took MetadataPosition
    # as the directory and UpdatePending as the metadata — and *nothing broke*,
    # because the directory read then failed, ``read_czi_layout`` fell back to
    # walking the segments, and the walk recovers both. Every file took the
    # damaged-file recovery path, carried its warning, and paid a whole pass
    # over itself; the suite stayed green throughout. A name cannot be off by
    # one.
    return directory_position, metadata_position


def _read_subblock_directory(fh: BinaryIO, position: int) -> list[SubBlock]:
    """Every directory entry, pyramid copies included (filtered by the caller)."""
    fh.seek(position)
    sid, _allocated, used = _SEGMENT_HEADER.unpack(
        _read_exactly(fh, _SEGMENT_HEADER.size, "subblock directory"))
    if _token(sid) != "ZISRAWDIRECTORY":
        raise CziReadError(
            f"The file header points at a {_token(sid)!r} segment where its "
            f"subblock directory should be. The file is damaged."
        )
    body = _read_exactly(fh, used, "subblock directory")
    (count,) = struct.unpack_from("<i", body, 0)
    if count < 0:
        raise CziReadError("The subblock directory declares a negative entry "
                           "count; the file is damaged.")
    blocks: list[SubBlock] = []
    offset = _DIRECTORY_HEADER_LEN
    for _ in range(count):
        block, offset = _parse_directory_entry(body, offset)
        blocks.append(block)
    return blocks


def _read_metadata_xml(fh: BinaryIO, position: int) -> str:
    """The metadata segment's XML, or ``""`` when the file carries none."""
    if position <= 0:
        return ""
    fh.seek(position)
    header = fh.read(_SEGMENT_HEADER.size)
    if len(header) != _SEGMENT_HEADER.size:
        return ""
    sid, _allocated, _used = _SEGMENT_HEADER.unpack(header)
    if _token(sid) != "ZISRAWMETADATA":
        return ""
    prologue = fh.read(8)
    if len(prologue) != 8:
        return ""
    xml_size, _attachment_size = struct.unpack("<ii", prologue)
    if xml_size <= 0:
        return ""
    fh.seek(position + _SEGMENT_HEADER.size + _METADATA_HEADER_LEN)
    raw = fh.read(xml_size)
    return raw.decode("utf-8", "replace")


def _walk_segments(fh: BinaryIO) -> tuple[list[SubBlock], int]:
    """Scan the file segment by segment: the fallback when the directory is gone.

    A CZI written by a session that was killed can carry every subblock it ever
    wrote and a directory offset of zero, because the directory is written last.
    The subblocks themselves are self-describing — each repeats its own
    directory entry — so the plate is still readable; it just costs a pass over
    the file instead of one seek. Returns the blocks and the metadata position
    found on the way (0 when there is none).
    """
    fh.seek(0, 2)
    end = fh.tell()
    fh.seek(0)
    blocks: list[SubBlock] = []
    metadata_position = 0
    position = 0
    while position + _SEGMENT_HEADER.size <= end:
        fh.seek(position)
        header = fh.read(_SEGMENT_HEADER.size)
        if len(header) != _SEGMENT_HEADER.size:
            break
        sid, allocated, used = _SEGMENT_HEADER.unpack(header)
        name = _token(sid)
        if allocated <= 0:
            # A zero-length segment cannot be stepped over, so the scan would
            # spin here for ever. Stop and report what was found.
            break
        if name == "ZISRAWSUBBLOCK":
            body = fh.read(min(used, _SUBBLOCK_MIN_HEADER * 4))
            if len(body) > _SUBBLOCK_PROLOGUE.size:
                try:
                    block, _ = _parse_directory_entry(
                        body, _SUBBLOCK_PROLOGUE.size)
                except CziReadError:
                    block = None
                if block is not None:
                    blocks.append(SubBlock(
                        file_position=position, pixel_type=block.pixel_type,
                        compression=block.compression,
                        pyramid_type=block.pyramid_type,
                        file_part=block.file_part, dims=block.dims))
        elif name == "ZISRAWMETADATA":
            metadata_position = position
        position += _SEGMENT_HEADER.size + allocated
    return blocks, metadata_position


# ── Metadata ─────────────────────────────────────────────────────────────────

def _localname(tag: str) -> str:
    """An element's tag without its XML namespace."""
    return tag.rsplit("}", 1)[-1]


def _find_all(root, name: str) -> list:
    """Every descendant whose local name is *name*, namespace notwithstanding.

    CZI metadata is namespace-free in every file seen so far, but that is a
    property of the writers rather than of the format, and an ``ElementTree``
    path match is exact: one namespaced document would silently yield no
    channels and no scenes, which reads as a file that has none.

    One lookup is one complete traversal, so anything asking the same document
    more than one question goes through :class:`_DocIndex` instead. This
    remains for the single-question case.
    """
    return [el for el in root.iter() if _localname(el.tag) == name]


class _DocIndex:
    """A metadata document's elements, bucketed by local name in one pass.

    A ZEN plate document is not small: it names every scene, and for a tiled
    acquisition it records each tile's stage position under the scene that
    holds it, so the element count scales with the acquisition rather than
    with the plate. Reading a header costs about 100 ms per megabyte of this
    document and nothing at all for the file's pixels — and in a split-scene
    export every file carries the *whole plate's* document, so a 661-file
    export parses the same thing 661 times per pass.

    Against that, a lookup that walks the tree is the whole cost. Three
    root-level questions — the scenes, the declared scene count, the channels
    — were three complete traversals of a document already in memory. This
    builds the buckets once and answers each of them from a dict.
    """

    __slots__ = ("_by_name",)

    def __init__(self, root) -> None:
        by_name: dict[str, list] = {}
        for element in root.iter():
            by_name.setdefault(_localname(element.tag), []).append(element)
        self._by_name = by_name

    def all(self, name: str) -> list:
        """Every element whose local name is *name*, in document order."""
        return self._by_name.get(name, [])


@dataclass(frozen=True)
class _PlateMetadata:
    """Everything this module asks of a CZI's plate metadata document.

    A value object rather than the parsed tree, because it is what gets
    cached and the tree is the expensive thing being avoided: a ZEN plate
    document runs to tens of megabytes and ``ET.fromstring`` is four fifths of
    a header read's cost, while these few lists are kilobytes.
    """

    has_metadata: bool = False
    #: ``{scene index: well label}`` — see :func:`_scene_wells`.
    scene_wells: dict = field(default_factory=dict)
    declared_scene_indices: tuple = ()
    channel_names: tuple = ()
    #: Why the document could not be parsed, for the layout to warn with.
    parse_error: str = ""
    #: What reading the document cost, counted at the read that took it.
    bytes_read: int = 0


def parse_plate_metadata(xml: str, *, bytes_read: int = 0) -> _PlateMetadata:
    """Answer every question this module asks of *xml*, in one parse."""
    if not xml:
        return _PlateMetadata(has_metadata=False, bytes_read=bytes_read)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return _PlateMetadata(has_metadata=False, parse_error=str(exc),
                              bytes_read=bytes_read)
    index = _DocIndex(root)
    return _PlateMetadata(
        has_metadata=True,
        scene_wells=_scene_wells(index),
        declared_scene_indices=tuple(_declared_scene_indices(index)),
        channel_names=tuple(_channel_names(index)),
        bytes_read=bytes_read,
    )


#: How many files' metadata to remember. A plate is at most a few hundred
#: files and the entries are kilobytes each, so this is sized to hold several
#: plates rather than to be a constraint anybody meets.
_METADATA_CACHE_MAX = 4096

_METADATA_CACHE: "OrderedDict[tuple, _PlateMetadata]" = OrderedDict()
#: The pool that reads a plate is eight threads wide, and ``OrderedDict``'s
#: move-to-end and evict are each several operations. Guarded rather than
#: trusted to the GIL, which does not make a read-modify-write atomic.
_METADATA_LOCK = threading.Lock()


def _file_stamp(path: Path) -> "tuple | None":
    """A key that changes whenever *path*'s contents could have, or ``None``.

    Three parts, each because of what it invalidates, since a reuse decision
    keyed on less than everything that makes the old answer wrong is how a
    stale number gets reported as a fresh one (CLAUDE.md §7.7 #3):

    * the **path**, because a different file is a different document;
    * the **size**, because a re-export is almost never the same length; and
    * the **modification time in nanoseconds**, which catches the re-export
      that is. Nanoseconds because a whole second is long enough for a writer
      to replace a file within one, and both platforms this ships to record
      finer than that — NTFS to 100 ns, APFS to 1 ns.

    Deliberately **not** a hash of the contents: reading the file is the cost
    this exists to avoid, so a key that reads it would be self-defeating.
    ``None`` when the file cannot be stat'd at all, which is not an error here
    — it means this read is simply not cached, and the caller reads on.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (str(path), stat.st_size, stat.st_mtime_ns)


def cached_plate_metadata(path: Path) -> "_PlateMetadata | None":
    """*path*'s parsed metadata if it has been read already, else ``None``."""
    stamp = _file_stamp(path)
    if stamp is None:
        return None
    with _METADATA_LOCK:
        found = _METADATA_CACHE.get(stamp)
        if found is not None:
            _METADATA_CACHE.move_to_end(stamp)
        return found


def remember_plate_metadata(path: Path, meta: _PlateMetadata) -> None:
    """Keep *meta* as *path*'s metadata until the file changes underneath it."""
    stamp = _file_stamp(path)
    if stamp is None:
        return
    with _METADATA_LOCK:
        _METADATA_CACHE[stamp] = meta
        _METADATA_CACHE.move_to_end(stamp)
        while len(_METADATA_CACHE) > _METADATA_CACHE_MAX:
            _METADATA_CACHE.popitem(last=False)


_LAYOUT_CACHE: "OrderedDict[tuple, CziLayout]" = OrderedDict()


def cached_layout(path: Path, read_times: bool) -> "CziLayout | None":
    """*path*'s layout if it has been read already, else ``None``.

    Handed back as a **copy**. A layout is aggregate data — a few lists and
    counts, never the subblocks themselves — so copying it is cheap, and it
    makes it impossible for a caller that mutates what it was given to poison
    the next caller's answer. Nothing in the tree does that today; the copy is
    so that nothing has to keep not doing it.

    *read_times* is part of the key because it changes the answer: a layout
    read without it carries no ``time_scan`` at all, and serving one to a
    caller that asked for the times would report a plate as having none.
    """
    stamp = _file_stamp(path)
    if stamp is None:
        return None
    with _METADATA_LOCK:
        found = _LAYOUT_CACHE.get((stamp, read_times))
        if found is not None:
            _LAYOUT_CACHE.move_to_end((stamp, read_times))
    return copy.deepcopy(found) if found is not None else None


def remember_layout(path: Path, read_times: bool, layout: "CziLayout") -> None:
    """Keep *layout* as *path*'s until the file changes underneath it."""
    stamp = _file_stamp(path)
    if stamp is None:
        return
    with _METADATA_LOCK:
        _LAYOUT_CACHE[(stamp, read_times)] = copy.deepcopy(layout)
        _LAYOUT_CACHE.move_to_end((stamp, read_times))
        while len(_LAYOUT_CACHE) > _METADATA_CACHE_MAX:
            _LAYOUT_CACHE.popitem(last=False)


def forget_plate_metadata() -> None:
    """Drop every remembered document. For tests, and for a caller that wants
    to prove a read really happened rather than trust that it did."""
    with _METADATA_LOCK:
        _METADATA_CACHE.clear()
        _LAYOUT_CACHE.clear()


#: A scene name that carries a well inside a longer label — ``P1-A1``,
#: ``Scene-3-B07``, ``A1-01``. Anchored on a separator so ``H12`` is read as a
#: well but the ``C1`` inside ``SPECIMENC12`` is not.
_EMBEDDED_WELL_RE = re.compile(r"(?:^|[-_ ])([A-Ha-h](?:0?[1-9]|1[0-2]))(?:$|[-_ ])")


def well_from_scene_name(name: str) -> str:
    """The 96-well label *name* denotes, or ``""``.

    Zeiss plate software names a scene after the well it scanned, but not always
    on its own: ``A1``, ``P1-A1`` and ``Scene-1-A1`` all appear. A name that
    does not resolve to a well returns ``""`` rather than a guess — the caller
    then falls back to the grid, which is wrong in an obvious way rather than a
    plausible one.
    """
    text = str(name or "").strip()
    if not text:
        return ""
    direct = canonical_well_label(text)
    if direct:
        return direct
    match = _EMBEDDED_WELL_RE.search(text)
    return canonical_well_label(match.group(1)) if match else ""


def _scene_wells(index: "_DocIndex") -> dict[int, str]:
    """``{scene index: well label}`` for the scenes whose metadata names one.

    Three places a writer puts it, tried in order of how specific they are:
    ``<Shape Name="A1">`` (the plate-well shape the scene was taken from),
    ``<ArrayName>A1</ArrayName>``, and the scene's own ``Name``. The first two
    are only ever a well; the third is a free-text label that merely often is
    one, so it is consulted last.

    Both of those are collected in **one** walk of the scene's subtree. Asking
    for each separately walked it twice, and the scene subtrees are where a
    tiled acquisition's bulk lives, so the two passes together came to rather
    more than one traversal of the whole document.
    """
    wells: dict[int, str] = {}
    for scene in index.all("Scene"):
        try:
            scene_index = int(scene.get("Index", ""))
        except (TypeError, ValueError):
            continue
        shapes: list[str] = []
        array_names: list[str] = []
        for element in scene.iter():
            local = _localname(element.tag)
            if local == "Shape":
                shapes.append(element.get("Name", ""))
            elif local == "ArrayName":
                array_names.append(element.text or "")
        for candidate in (*shapes, *array_names, scene.get("Name", "")):
            well = well_from_scene_name(candidate)
            if well:
                wells[scene_index] = well
                break
    return wells


def _declared_scene_indices(index: "_DocIndex") -> list[int]:
    """The scene indices the metadata lists, ascending, or ``[]``.

    Read from the ``<Scenes>`` list rather than from the subblock directory,
    which is what makes it answerable from the file's header alone. A file that
    lists no scenes and declares no ``SizeS`` has one scene — that is what a
    CZI with no S axis is — so the empty list here and "one unnamed scene" are
    told apart by the caller, which knows whether any metadata was read at all.
    """
    indices: list[int] = []
    for scene in index.all("Scene"):
        try:
            indices.append(int(scene.get("Index", "")))
        except (TypeError, ValueError):
            continue
    if indices:
        return sorted(set(indices))
    for element in index.all("SizeS"):
        try:
            count = int((element.text or "").strip())
        except (TypeError, ValueError):
            continue
        if count > 0:
            return list(range(count))
    return [0]


def wells_from_scenes(path: Path, scene_wells: dict[int, str],
                      scenes: list[int], *,
                      from_filename: bool = True) -> list[str]:
    """One well per scene of *path*, in scene order; ``""`` where unknown.

    The scene's own metadata first, because that is the acquisition saying
    which well it scanned. Failing that, and **only for a file holding exactly
    one scene**, the filename: ZEN's split-scene export names each file after
    the scene it holds (``Exp7_A1.czi``), and a file that *is* a scene may
    legitimately be identified by its name. The one-scene condition is what
    keeps that from misreading a whole-plate file that happens to be called
    ``Exp_A1.czi`` as a single well of ninety-six fields.

    *from_filename* is what the caller sets to False for a **lone** file: the
    rule exists for ZEN's split export, where each member is named after the
    scene it holds, and a single file gives it nothing to corroborate it with.

    One function for both routes into this question — the full layout read and
    the header-only peek — because the peek exists to paint a plate that the
    full read then confirms, and two derivations of one rule would eventually
    paint a plate the run contradicts.
    """
    labels = [scene_wells.get(scene, "") for scene in scenes]
    if from_filename and len(labels) == 1 and not labels[0]:
        labels = [well_from_scene_name(path.stem)]
    return labels


class _CountingReader:
    """A read-only view of a file handle that tallies the bytes taken from it.

    Wrapping rather than counting by arithmetic: the tally is the number a page
    shows a user, and one computed from what the reads *should* have been stops
    being true the moment a read changes without the sum being updated — which
    is the kind of number nobody re-derives and everybody quotes.
    """

    __slots__ = ("_fh", "bytes_read")

    def __init__(self, fh: BinaryIO) -> None:
        self._fh = fh
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self._fh.read(size)
        self.bytes_read += len(data)
        return data

    def seek(self, *args, **kwargs):
        return self._fh.seek(*args, **kwargs)

    def tell(self) -> int:
        return self._fh.tell()


@dataclass(frozen=True)
class ScenePeek:
    """What a CZI's *header* says about its scenes, with nothing else read.

    The answer to "which wells does this file hold?" costs the 112-byte file
    header and the metadata segment it points at — a few kilobytes — against
    the ~1 MB subblock directory a full layout read walks, and neither touches
    a pixel. On a plate held on a network share that is the difference between
    painting a plate map at once and waiting on the directory of every file.

    ``wells`` is one entry per scene in scene order, ``""`` where the file does
    not say. ``has_metadata`` is False when the file carries no readable
    metadata at all, which is **not** the same as carrying metadata that names
    no wells. The difference decides whether the filename may stand in for a
    scene name: that is only allowed for a file holding exactly one scene, and
    with no metadata a peek cannot tell one scene from ninety-six. So such a
    file contributes nothing here and waits for the full read, rather than
    being painted onto a plate the run would then contradict.

    ``bytes_read`` is what it cost — **counted**, not derived from the sizes of
    the structures involved. A figure worked out by adding up what the reads
    ought to have been is a number that drifts silently the first time a read
    changes, and its whole job is to be the honest answer to "what did this
    cost?".
    """

    path: Path
    wells: list[str] = field(default_factory=list)
    has_metadata: bool = True
    bytes_read: int = 0


def peek_scene_wells(path: Path | str, *,
                     from_filename: bool = True) -> ScenePeek:
    """Read *path*'s scene-to-well mapping from its header alone.

    Reads the file header and the metadata segment it points at, and **never**
    the subblock directory. So it cannot say whether the file is sound, how
    many tiles it holds, or what its pixel format is — only what its metadata
    claims about its scenes. :func:`read_czi_layout` answers the rest, and a
    caller that needs an answer it can convert against has to ask it.

    The document is read and parsed once per file and then remembered against
    that file's size and modification time, because the plate is read more
    than once before a single archive is written — the page peeks, then reads
    in full, and the run reads again — and a split-scene export puts the whole
    plate's document in every one of its files. *from_filename* is applied
    after the cache rather than baked into it, since it changes what the
    scenes are *called* and not what the document says.
    """
    path = Path(path)
    meta = cached_plate_metadata(path)
    if meta is None:
        with open(path, "rb") as raw:
            fh = _CountingReader(raw)
            directory_position, metadata_position = _read_file_header(fh)
            del directory_position                  # deliberately not read
            xml = _read_metadata_xml(fh, metadata_position)
            read = fh.bytes_read
        meta = parse_plate_metadata(xml, bytes_read=read)
        remember_plate_metadata(path, meta)
    if not meta.has_metadata:
        return ScenePeek(path=path, has_metadata=False,
                         bytes_read=meta.bytes_read)
    wells = wells_from_scenes(path, dict(meta.scene_wells),
                              list(meta.declared_scene_indices),
                              from_filename=from_filename)
    return ScenePeek(path=path, wells=wells, bytes_read=meta.bytes_read)


#: The subblock-metadata tag ZEN stamps a plane's wall-clock acquisition time
#: into. Read case-insensitively by local name, because the metadata block is
#: namespaced in some writers and not in others, and a tag this reader cannot
#: find costs nothing but the positional fallback.
_ACQUISITION_TIME_TAGS = ("acquisitiontime",)

#: How many bytes to pull in one go when sampling a subblock's metadata. The
#: segment header, the padded prologue and the metadata XML sit contiguously,
#: so one window of this size usually takes all three in a single round trip
#: instead of three. Measured on a 96-scene plate over an 8 ms link: 12 trips
#: and 98 ms for the coalesced read against 24 trips and 196 ms for the
#: three-read version. Oversized reads are free locally and cheap on a share,
#: where the round trip is the cost and the bytes are not.
_SUBBLOCK_META_WINDOW = 4096


def read_subblock_metadata(fh: BinaryIO, block: SubBlock) -> str:
    """One subblock's own metadata XML, read without touching its pixels.

    A subblock stores its metadata between its header and its image data, at an
    offset that depends on how many dimensions the tile declares — the same
    arithmetic :meth:`CziReader._read_block_data` performs to seek *past* it.
    One window is pulled in a single read and the XML sliced out of it; only a
    metadata block larger than the window costs a second round trip.
    """
    fh.seek(block.file_position)
    window = fh.read(_SUBBLOCK_META_WINDOW)
    if len(window) < _SEGMENT_HEADER.size + _SUBBLOCK_PROLOGUE.size:
        return ""
    sid = _token(_SEGMENT_HEADER.unpack_from(window, 0)[0])
    if sid != "ZISRAWSUBBLOCK":
        return ""
    metadata_size, _attachment, _data = _SUBBLOCK_PROLOGUE.unpack_from(
        window, _SEGMENT_HEADER.size)
    if metadata_size <= 0:
        return ""
    entry_size = _DIRECTORY_ENTRY.size + _DIMENSION_ENTRY.size * len(block.dims)
    header_len = max(_SUBBLOCK_MIN_HEADER, _SUBBLOCK_PROLOGUE.size + entry_size)
    start = _SEGMENT_HEADER.size + header_len
    if start + metadata_size <= len(window):
        raw = window[start:start + metadata_size]
    else:
        fh.seek(block.file_position + start)
        raw = fh.read(metadata_size)
    return raw.decode("utf-8", "replace")


def normalise_instant(text: str) -> str:
    """An ISO 8601 instant in the spelling every supported Python parses.

    ZEN writes ``2024-06-12T10:23:45.4830000Z``: a trailing ``Z`` for UTC and a
    fractional second of seven digits. **Python 3.11 accepts both** — its
    ``fromisoformat`` was relaxed to take arbitrary fractional digits and a
    military zone suffix — so on the interpreter this app runs today the
    rewriting below changes nothing that would otherwise fail. It is here for
    the 3.10 build environment the PyInstaller spec still falls back to, where
    ``fromisoformat`` takes three or six fractional digits and no ``Z``, and
    where an unrewritten stamp would send the whole plate to positional tokens.

    Tested on its own output rather than through
    :func:`parse_acquisition_time`, because 3.11's parser is permissive enough
    to swallow the un-normalised form and make any end-to-end assertion pass
    either way (CLAUDE.md §7.8: name the wrong implementation that still
    passes).
    """
    if not text:
        return text
    out = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    match = re.match(r"^(.*?[T ]\d{2}:\d{2}:\d{2})\.(\d+)(.*)$", out)
    if match:
        head, fraction, tail = match.groups()
        out = f"{head}.{fraction[:6].ljust(6, '0')}{tail}"
    return out


def parse_acquisition_time(xml: str) -> "datetime | None":
    """The wall-clock time a subblock's metadata stamps on its plane.

    The instant is normalised by :func:`normalise_instant` first, then parsed.

    ``None`` for anything else, including a tag that is absent. Every caller
    treats that as a reason to name the timepoints by position, so a
    misidentified tag costs the elapsed times and never a wrong number.
    """
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    raw = ""
    for element in root.iter():
        if _localname(element.tag).lower() in _ACQUISITION_TIME_TAGS:
            raw = (element.text or "").strip()
            if raw:
                break
    if not raw:
        return None
    text = normalise_instant(raw)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _representative_blocks(blocks: list[SubBlock],
                           size_t: int) -> tuple[int, dict[int, SubBlock]]:
    """``(scene, {t: block})`` for the first scene present at every timepoint.

    **The first well scanned**, which is the same rule the OME converter's
    ``hours.setdefault(ti, …)`` applies to the first plane it meets: one
    position's series, read in the order the file stores it. It has to be one
    position, because a plate scan reaches its wells minutes apart within a
    single round — measured at 31 minutes across 96 scenes — so a series
    assembled from whichever scene happened to sort first at each timepoint
    folds that traverse into the elapsed times it reports.

    First *in the file's own subblock order* rather than by lowest index: that
    order is acquisition order, so the scene it picks is the well the stage
    actually visited first. A scene missing from any timepoint is skipped
    rather than accepted with a gap, since the gap would be filled by another
    scene's clock.
    """
    per_scene: dict[int, dict[int, SubBlock]] = {}
    order: list[int] = []
    for block in blocks:
        if block.is_pyramid:
            continue
        scene = block.start("S")
        if scene not in per_scene:
            per_scene[scene] = {}
            order.append(scene)
        per_scene[scene].setdefault(block.start("T"), block)
    for scene in order:
        found = per_scene[scene]
        if all(t in found for t in range(size_t)):
            return scene, {t: found[t] for t in range(size_t)}
    return -1, {}


def read_timepoint_hours(path: Path | str, blocks: list[SubBlock],
                         size_t: int) -> TimeScan:
    """Elapsed hours per timepoint, from one position's acquisition stamps.

    Costs one seek and one short read per timepoint — twelve round trips for a
    twelve-point run, whatever the plate weighs — because the subblock
    directory already says where every tile is. Reading *every* subblock's
    metadata instead would be one round trip per tile: 9,216 of them on a
    96-scene plate, measured at 75 s over an 8 ms link against 98 ms for this.

    Never raises. Every failure — an unreadable file, a missing tag, a stamp
    that will not parse — comes back as an empty or partial
    :class:`TimeScan` with the reason written down, because the caller's
    fallback (positional ``t0``/``t1`` tokens) is always available and always
    correct.
    """
    path = Path(path)
    try:
        with open(path, "rb") as raw:
            return scan_timepoint_hours(raw, blocks, size_t)
    except OSError as exc:
        return TimeScan(reasons=[f"the file could not be read for its "
                                 f"acquisition times ({exc})"])


def scan_timepoint_hours(fh: BinaryIO, blocks: list[SubBlock],
                         size_t: int) -> TimeScan:
    """:func:`read_timepoint_hours` against an already-open handle.

    The form :func:`read_czi_layout` uses, so sampling the stamps costs the
    seeks and not a second open and a second walk of the subblock directory.
    Leaves the handle's position wherever the last sample put it, which is why
    it is called after everything else that reads from it.
    """
    if size_t <= 0:
        return TimeScan()
    scene, chosen = _representative_blocks(blocks, size_t)
    if not chosen:
        return TimeScan(reasons=[
            "no single scene holds a tile at every timepoint, so no one "
            "position's clock covers the run"])
    counted = _CountingReader(fh)
    stamps: dict[int, datetime] = {}
    try:
        for t_index, block in sorted(chosen.items()):
            stamp = parse_acquisition_time(
                read_subblock_metadata(counted, block))
            if stamp is not None:
                stamps[t_index] = stamp
    except OSError as exc:
        return TimeScan(reasons=[f"the file could not be read for its "
                                 f"acquisition times ({exc})"], scene=scene)
    read = counted.bytes_read

    if 0 not in stamps:
        return TimeScan(reasons=[
            f"{len(stamps)} of {size_t} timepoint(s) carry a readable "
            f"acquisition time, and the first is not among them, so there is "
            f"no zero to measure the rest from"], scene=scene, bytes_read=read)
    # Mixing a naive stamp with an aware one raises on subtraction, and a file
    # that spells its instants two ways has not told us which clock either is
    # on. Refusing the lot is the only reading that cannot be wrong.
    aware = {t: (s.tzinfo is not None) for t, s in stamps.items()}
    if len(set(aware.values())) > 1:
        return TimeScan(reasons=[
            "the acquisition times mix zoned and unzoned instants, so the "
            "intervals between them are not decidable"], scene=scene,
            bytes_read=read)
    origin = stamps[0]
    hours = {t: (stamp - origin).total_seconds() / 3600.0
             for t, stamp in stamps.items()}
    reasons: list[str] = []
    if len(hours) < size_t:
        reasons.append(
            f"only {len(hours)} of {size_t} timepoint(s) carry a readable "
            f"acquisition time")
    return TimeScan(hours_by_index=hours, reasons=reasons, scene=scene,
                    bytes_read=read)


def _channel_names(index: "_DocIndex") -> list[str]:
    """Channel display names in channel-index order, or ``[]``.

    Read from ``<Dimensions><Channels><Channel>``, whose ``Id`` is
    ``Channel:<n>`` — used when present so a file listing its channels out of
    order still lands each name on the right index. ``Name`` is preferred over
    the fluorophore because it is what the user chose in the acquisition
    software, and it is what they will be looking for in Analyze's channel rows.
    """
    named: dict[int, str] = {}
    unindexed: list[str] = []
    for order, channel in enumerate(index.all("Channel")):
        name = (channel.get("Name")
                or (channel.findtext("Fluor") or "").strip()
                or (channel.findtext("DyeName") or "").strip())
        if not name:
            continue
        match = re.search(r"Channel:(\d+)", channel.get("Id", "") or "")
        index = int(match.group(1)) if match else order
        if index in named:
            unindexed.append(name)
            continue
        named[index] = name
    if not named:
        return []
    out = [named[i] for i in sorted(named)]
    return out + unindexed


# ── Layout ───────────────────────────────────────────────────────────────────

@dataclass
class CziLayout:
    """What a CZI holds, read from its subblock directory and its metadata.

    The directory is the authority on *shape*: it lists every tile the file
    actually stores, so a count taken from it cannot claim a plane the file does
    not have. The metadata is the authority on *names* — channels, and the well
    each scene was taken from — none of which the directory records.

    ``scenes`` and ``tiles`` together are the position axis: one scene per well
    on a plate scan, one tile per field of view within it.
    """

    scenes: list[int] = field(default_factory=list)
    #: scene index → its mosaic-tile indices, ascending. One entry per scene.
    tiles: dict[int, list[int]] = field(default_factory=dict)
    size_t: int = 1
    size_c: int = 1
    size_z: int = 1
    width: int = 0
    height: int = 0
    dtype: str = ""
    #: Filename tokens, one per channel index — already through
    #: :func:`~util.filename_token.safe_token`.
    channel_names: list[str] = field(default_factory=list)
    #: The same channels as the metadata spells them, kept only so a token
    #: collision can name the two channels the user has to go and rename.
    raw_channel_names: list[str] = field(default_factory=list)
    #: scene index → the well its metadata names. Missing for a scene the file
    #: does not name, which is why this is a mapping and not a list.
    scene_wells: dict[int, str] = field(default_factory=dict)
    compressions: set[int] = field(default_factory=set)
    pixel_types: set[int] = field(default_factory=set)
    #: Axes beyond S/M/T/C/Z/X/Y that the file uses with an extent above 1,
    #: as ``{axis: extent}``. Each is a set of planes this importer has no
    #: filename field for, so they are refused rather than collapsed.
    extra_axes: dict[str, int] = field(default_factory=dict)
    #: Acquisition tiles in the directory, pyramid copies already excluded.
    n_blocks: int = 0
    #: Tiles the directory holds twice for one coordinate. Non-zero means the
    #: index cannot say which of them is the plane, so the file is refused.
    duplicate_blocks: int = 0
    #: Tiles whose pixels live in a *sibling* file — ZEN splits a very large
    #: acquisition into numbered parts that share one subblock directory. This
    #: reader opens one file, so their offsets would land at arbitrary
    #: positions in the wrong file; refused rather than read.
    foreign_part_blocks: int = 0
    #: Images a conversion of this file will write: one per
    #: (scene, tile, timepoint, channel), because Z is collapsed into each.
    #: Counted as images rather than as stored tiles on purpose — see
    #: :attr:`missing_images`.
    total_images: int = 0
    #: Of those, the ones the file holds no Z plane at all for. The count is
    #: **not** ``total - stored_tiles``: that arithmetic treats the per-axis
    #: maxima as a rectangle, so a perfectly ordinary acquisition with a
    #: 3-plane stack on the fluorescent channel and a single brightfield plane
    #: reads as missing two thirds of its brightfield. Every plane is present;
    #: only the *stack depths* differ, and Z is projected anyway. The same
    #: reasoning keeps Z out of :meth:`_ragged_scenes`, and
    #: :mod:`pipeline.ome_explode` excludes Z from its own ragged check for it.
    missing_images: int = 0
    has_metadata: bool = True
    #: Whether the directory segment was readable, or the file had to be walked
    #: segment by segment to find its tiles.
    from_directory: bool = True
    #: Elapsed hours per timepoint, sampled from one position's acquisition
    #: stamps — ``None`` when this layout was read without asking for them,
    #: which is every file of a set but the first. See
    #: :func:`scan_timepoint_hours`.
    time_scan: "TimeScan | None" = None
    warnings: list[str] = field(default_factory=list)

    @property
    def n_scenes(self) -> int:
        return len(self.scenes)

    @property
    def n_positions(self) -> int:
        """Scene × tile pairs: the acquisition's stage positions."""
        return sum(len(self.tiles.get(s, [0])) for s in self.scenes)

    @property
    def fovs_per_scene(self) -> int:
        """Tiles per scene when every scene has the same number, else ``0``."""
        counts = {len(self.tiles.get(s, [0])) for s in self.scenes}
        return counts.pop() if len(counts) == 1 else 0

    @property
    def timepoint_tokens(self) -> list[str]:
        """``t0``, ``t1``, … — positional, in acquisition order.

        Deliberately not clock time. A CZI records a plane's acquisition time
        in each *subblock's* own metadata, which means reading every subblock's
        XML to learn it, and the file-level ``<T><Positions>`` interval is an
        acquisition *plan* rather than what happened. Positional tokens order
        correctly and state no number the file cannot back up; ARCHITECTURE's
        Convert CZI section and the manual both say so, because a user
        expecting hours on a Review axis needs to know where they went.
        """
        return [f"t{t}" for t in range(self.size_t)]

    def wells_from_metadata(self) -> list[str]:
        """One well per scene, in scene order — or ``[]`` if they cannot be used.

        All or nothing, and distinct. A partial mapping is the worst of the
        three outcomes: the named scenes would land on their real wells and the
        rest on wherever the grid put them, producing a plate that is right in
        the places a user is most likely to spot-check. Two scenes claiming one
        well is the same failure by a different route — the second scene's
        images would overwrite the first's archive.
        """
        labels = [self.scene_wells.get(s, "") for s in self.scenes]
        if not labels or not all(labels) or len(set(labels)) != len(labels):
            return []
        return labels

    def _colliding_channels(self) -> tuple[str, list[str]] | None:
        """The first filename-token collision among the channels, or ``None``.

        Two channels whose names reduce to one token write the same member name
        into one archive. ``zipfile`` stores both copies and a reader returns
        only the last, so one channel becomes permanently unreachable while the
        run reports having written both. Compared case-insensitively because
        every consumer of a channel token downstream folds case, so ``GFP`` and
        ``gfp`` collapse into one key even though they are two member names.
        """
        seen: dict[str, list[str]] = {}
        for i, token in enumerate(self.channel_names):
            raw = (self.raw_channel_names[i]
                   if i < len(self.raw_channel_names) else token)
            seen.setdefault(str(token).lower(), []).append(str(raw))
        for token, names in seen.items():
            if len(names) > 1:
                return token, names
        return None

    def _ragged_scenes(self) -> tuple[int, int, int, int] | None:
        """``(scene, its tiles, first scene, its tiles)`` when they disagree."""
        if len(self.scenes) < 2:
            return None
        first = self.scenes[0]
        want = len(self.tiles.get(first, [0]))
        for scene in self.scenes[1:]:
            got = len(self.tiles.get(scene, [0]))
            if got != want:
                return scene, got, first, want
        return None

    def validation_report(self, name: str = "") -> str:
        """The one blocking problem with this file, or ``""`` if it is sound.

        Ordered by how far upstream the fault is, so the message names the
        thing a user has to change rather than a consequence of it: a file
        whose pixels cannot be decoded has nothing to say about its well grid.
        """
        subject = name or "This file"
        if not self.n_blocks:
            return (
                f"{subject} holds no image tiles. Either the acquisition was "
                f"stopped before it wrote any, or the file is a CZI container "
                f"of something other than images."
            )
        bad_pixels = sorted(t for t in self.pixel_types if t not in PIXEL_DTYPES)
        if bad_pixels:
            named = ", ".join(_UNSUPPORTED_PIXEL_TYPES.get(t, f"type {t}")
                              for t in bad_pixels)
            return (
                f"{subject} stores its images as {named}, which is a colour or "
                f"complex pixel format rather than the single intensity value "
                f"per pixel this pipeline quantifies. Export the acquisition "
                f"channels as greyscale from ZEN and convert that instead."
            )
        if len(self.pixel_types) > 1:
            return (
                f"{subject} mixes {len(self.pixel_types)} different pixel "
                f"formats across its tiles, so the images it would produce "
                f"would not be comparable with each other. Re-export it from "
                f"ZEN as a single format."
            )
        refused = sorted(c for c in self.compressions if c in (1,))
        if refused:
            return (
                f"{subject} stores its tiles as JPEG, which is lossy — the "
                f"intensities in it are not the ones the camera measured, so "
                f"quantifying them would report numbers the experiment did not "
                f"produce. Re-export it from ZEN uncompressed or with "
                f"Zstandard compression."
            )
        unknown = sorted(c for c in self.compressions
                         if c not in COMPRESSION_NAMES)
        if unknown:
            return (
                f"{subject} stores its tiles with compression method "
                f"{unknown[0]}, which this reader does not know how to decode. "
                f"Re-export it from ZEN uncompressed."
            )
        if self.extra_axes:
            axis, extent = sorted(self.extra_axes.items())[0]
            return (
                f"{subject} uses the {axis} dimension with {extent} positions. "
                f"Every image is named "
                f"<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif, which "
                f"has no field for {axis}, so those {extent} planes would all "
                f"be written to one filename and only the last would survive. "
                f"Split the {axis} dimension in ZEN and convert each part "
                f"separately."
            )
        if self.foreign_part_blocks:
            return (
                f"{subject} is one part of a multi-file CZI: "
                f"{self.foreign_part_blocks} of its tiles have their pixels in "
                f"companion files beside it, which this reader does not follow. "
                f"Re-export it from ZEN as a single file, or with the scenes "
                f"split into one file per scene — this converter reads either. "
                f"(A split-scene export is a folder of .czi files, and picking "
                f"any one of them offers the whole folder.)"
            )
        if self.duplicate_blocks:
            return (
                f"{subject} stores {self.duplicate_blocks} tile(s) twice under "
                f"the same scene/tile/timepoint/channel/Z coordinates, so which "
                f"of each pair is the image cannot be decided. The file has "
                f"most likely been concatenated from two acquisitions; convert "
                f"them separately."
            )
        ragged = self._ragged_scenes()
        if ragged:
            scene, got, first, want = ragged
            return (
                f"{subject} holds scenes with different numbers of tiles: "
                f"scene {first} has {want} and scene {scene} has {got}. Every "
                f"well has to carry the same fields of view for the plate to "
                f"line up. Re-export it, or export the affected scene "
                f"separately."
            )
        collision = self._colliding_channels()
        if collision:
            token, names = collision
            shown = ", ".join(repr(n) for n in names)
            return (
                f"{subject} has {len(names)} channels whose names all reduce to "
                f"the filename token {token!r}: {shown}. Every image is named "
                f"<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif, so "
                f"those would be written to the same name inside one archive "
                f"and only the last would be readable. Rename the channels in "
                f"ZEN so they differ by more than punctuation or spacing, and "
                f"export again."
            )
        if self.missing_images > 0:
            return (
                f"{subject} is incomplete: its scenes, tiles, timepoints and "
                f"channels describe {self.total_images} image(s), but "
                f"{self.missing_images} of them have no image data at all. "
                f"Converting would leave those missing from wells that "
                f"otherwise look complete. That usually means the acquisition "
                f"was stopped before it finished — re-export it with the "
                f"dimensions it actually holds."
            )
        return ""

    def describe(self) -> str:
        """A one-line summary of the file, for the page readout and the log."""
        channels = ", ".join(self.channel_names) if self.channel_names else "?"
        fovs = self.fovs_per_scene
        fov_text = f"{fovs} tile(s) each" if fovs else "a varying number of tiles"
        named = len(self.wells_from_metadata())
        source = (f", wells named by the file ({named})" if named
                  else ", wells from the grid you set")
        return (
            f"{self.n_scenes} scene(s), {fov_text}, {self.size_t} timepoint(s), "
            f"{self.size_c} channel(s) ({channels})"
            f"{f', {self.size_z} Z-plane(s) max-projected' if self.size_z > 1 else ''}"
            f", {self.width}×{self.height} {self.dtype}{source}."
        )


def _build_layout(blocks: list[SubBlock], meta: _PlateMetadata, *,
                  from_directory: bool) -> CziLayout:
    """Turn the directory entries (and the metadata, if any) into a layout."""
    layout = CziLayout(has_metadata=meta.has_metadata,
                       from_directory=from_directory)
    acquisition = [b for b in blocks if not b.is_pyramid]
    layout.n_blocks = len(acquisition)
    if not acquisition:
        return layout

    layout.foreign_part_blocks = sum(1 for b in acquisition if b.file_part)
    layout.pixel_types = {b.pixel_type for b in acquisition}
    layout.compressions = {b.compression for b in acquisition}
    layout.width = max(b.size("X") for b in acquisition)
    layout.height = max(b.size("Y") for b in acquisition)
    layout.dtype = PIXEL_DTYPES.get(sorted(layout.pixel_types)[0], "")

    for axis in sorted({ax for b in acquisition for ax in b.dims}):
        if axis in _KNOWN_AXES:
            continue
        extent = max(b.size(axis) for b in acquisition)
        starts = {b.start(axis) for b in acquisition}
        extent = max(extent, len(starts))
        if extent > 1 or axis not in _IGNORABLE_AXES:
            layout.extra_axes[axis] = extent

    scenes = sorted({b.start("S") for b in acquisition})
    layout.scenes = scenes
    tiles: dict[int, set[int]] = {s: set() for s in scenes}
    for block in acquisition:
        tiles[block.start("S")].add(block.start("M"))
    layout.tiles = {s: sorted(v) for s, v in tiles.items()}

    # ``max(start) + 1`` rather than the number of distinct values: a file that
    # skipped a timepoint still declares the ones after it at their real index,
    # and counting distinct values would quietly renumber them into a shorter,
    # complete-looking run. The shortfall is reported as missing planes instead.
    layout.size_t = max((b.start("T") for b in acquisition), default=0) + 1
    layout.size_c = max((b.start("C") for b in acquisition), default=0) + 1
    layout.size_z = max((b.start("Z") for b in acquisition), default=0) + 1

    keys = {(b.start("S"), b.start("M"), b.start("T"), b.start("C"), b.start("Z"))
            for b in acquisition}
    layout.duplicate_blocks = len(acquisition) - len(keys)
    # One image per (scene, tile, timepoint, channel) — Z collapses into it —
    # and an image is missing only when the file holds *no* plane for it. A
    # stack that is three deep on one channel and one deep on another is not
    # an incomplete file, and counting stored tiles against a rectangle said
    # it was.
    projections = {key[:4] for key in keys}
    layout.total_images = (layout.n_positions * layout.size_t * layout.size_c)
    layout.missing_images = max(0, layout.total_images - len(projections))

    if meta.parse_error:
        layout.warnings.append(
            f"metadata XML could not be parsed: {meta.parse_error}")
    elif meta.has_metadata:
        layout.scene_wells = dict(meta.scene_wells)
        layout.raw_channel_names = list(meta.channel_names)
    names = list(layout.raw_channel_names)
    layout.channel_names = [
        safe_token(names[i] if i < len(names) else "", f"c{i}")
        for i in range(layout.size_c)
    ]
    # Trim the raw list to the channels the file really has, so the two lists
    # line up index for index wherever a collision has to name one.
    layout.raw_channel_names = [
        names[i] if i < len(names) else layout.channel_names[i]
        for i in range(layout.size_c)
    ]
    return layout


def read_czi_layout(path: Path | str, *,
                    read_times: bool = False) -> CziLayout:
    """Read *path*'s layout without decoding a single pixel.

    Costs two seeks on a sound file: one to the header, one to the subblock
    directory it points at. A file whose directory is missing or unreadable —
    which is what an acquisition killed before it finished writing leaves — is
    walked segment by segment instead, and the layout records that it had to be.

    With *read_times* it also samples one position's acquisition stamps into
    :attr:`CziLayout.time_scan` — one further seek and short read per
    timepoint, taken here rather than in a second pass because the handle and
    the subblock list are already open and in hand. Off by default because a
    plate needs the timing from **one** of its files: a split-scene export's
    members all cover the same timepoints, so sampling all ninety-six would be
    ninety-six times the round trips for the same answer.
    """
    path = Path(path)
    # The plate is read more than once before an archive is written — the page
    # describes it, then the worker validates it, then ``explode_czi`` reads
    # it again — and on a network share each of those is a round trip and a
    # megabyte of subblock directory per file.
    already = cached_layout(path, read_times)
    if already is not None:
        return already
    with open(path, "rb") as fh:
        directory_position, metadata_position = _read_file_header(fh)
        blocks: list[SubBlock] = []
        from_directory = True
        if directory_position > 0:
            try:
                blocks = _read_subblock_directory(fh, directory_position)
            except CziReadError:
                blocks = []
        if not blocks:
            from_directory = False
            blocks, walked_metadata = _walk_segments(fh)
            if metadata_position <= 0:
                metadata_position = walked_metadata
        # Only when this file's document has not been parsed already. On a
        # hit this skips the read as well as the parse, which on a network
        # share is the larger half: the page's peek has normally just taken
        # it, and the document is the same bytes either way.
        meta = cached_plate_metadata(path)
        if meta is None:
            xml = _read_metadata_xml(fh, metadata_position)
            meta = parse_plate_metadata(xml, bytes_read=0)
            remember_plate_metadata(path, meta)
        layout_blocks = [b for b in blocks if not b.is_pyramid]
        scan = None
        if read_times:
            # After the metadata read, because it leaves the handle wherever
            # the last sample put it.
            scan = scan_timepoint_hours(
                fh, layout_blocks,
                max((b.start("T") for b in layout_blocks), default=-1) + 1)
    layout = _build_layout(blocks, meta, from_directory=from_directory)
    layout.time_scan = scan
    if not from_directory and layout.n_blocks:
        layout.warnings.append(
            "the subblock directory was unreadable, so the file was scanned "
            "tile by tile; this is what an interrupted acquisition leaves"
        )
    remember_layout(path, read_times, layout)
    return layout


# ── Decoding ─────────────────────────────────────────────────────────────────

def _codec(name: str):
    """One :mod:`imagecodecs` function, or a :class:`CziReadError` saying so.

    Imported here rather than at module scope so an uncompressed CZI — and the
    whole of this module's test coverage — needs nothing beyond numpy.
    imagecodecs ships with the pipeline (``requirements-remote.txt``), so this
    only bites in a partial environment, and then it says which package is
    missing instead of an ``ImportError`` from four frames down.
    """
    try:
        import imagecodecs
    except ImportError as exc:  # pragma: no cover — imagecodecs ships with the app
        raise CziReadError(
            "This CZI stores its tiles compressed, which needs the imagecodecs "
            "package; it is not installed in this environment. Install it "
            "(`pip install imagecodecs`), or re-export the file from ZEN "
            "uncompressed."
        ) from exc
    function = getattr(imagecodecs, name, None)
    if function is None:  # pragma: no cover — depends on the build's codecs
        raise CziReadError(
            f"The installed imagecodecs build has no {name} codec, so this "
            f"file's tiles cannot be decoded. Re-export it from ZEN "
            f"uncompressed."
        )
    return function


def _zstd1_payload(data: bytes) -> tuple[bytes, bool]:
    """Strip a Zstandard-1 tile's header: ``(payload, high_low_packed)``.

    A ``Zstd1`` tile opens with a small chunk header — a varint holding the
    header's own length, then id/value chunks — before the zstd frame. The one
    chunk that matters is id 1, whose low bit says the 16-bit image was split
    into a plane of high bytes and a plane of low bytes before compression.
    """
    size = 0
    shift = 0
    pos = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        size |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    if size <= 0 or size > len(data):
        # No readable header: treat the whole tile as a plain zstd frame, which
        # is what a writer that emits Zstd1 without chunks produces.
        return data, False
    packed = False
    while pos < size:
        chunk_id = data[pos]
        pos += 1
        if chunk_id == 1 and pos < size:
            packed = bool(data[pos] & 1)
            pos += 1
        else:
            break
    return data[size:], packed


def _decode_tile(data: bytes, block: SubBlock, *, width: int, height: int,
                 dtype: str) -> np.ndarray:
    """The pixels of one subblock, as a ``(height, width)`` array of *dtype*."""
    compression = block.compression
    if compression == 0:
        raw = data
    elif compression == 2:
        raw = bytes(_codec("lzw_decode")(data))
    elif compression == 4:
        decoded = np.asarray(_codec("jpegxr_decode")(data))
        decoded = np.squeeze(decoded)
        if decoded.ndim != 2:
            raise CziReadError(
                f"A JPEG-XR tile decoded to {decoded.ndim} dimensions rather "
                f"than a single 2-D image; this file's tiles are not plain "
                f"greyscale planes."
            )
        return _shaped(decoded.astype(dtype, copy=False), width, height)
    elif compression in (5, 6):
        payload, packed = (data, False)
        if compression == 6:
            payload, packed = _zstd1_payload(data)
        if packed:
            # Refused rather than guessed. Unpacking means interleaving a plane
            # of high bytes with a plane of low bytes, and getting that order
            # the wrong way round produces a complete, plausible image of
            # entirely wrong intensities — the one failure this importer must
            # not be able to have. No file exercising it was available to
            # establish the order against, so it says so instead.
            raise CziReadError(
                "This CZI stores its tiles with Zstandard high/low byte "
                "packing, which this reader cannot decode. Re-export it from "
                "ZEN uncompressed, or with plain Zstandard compression."
            )
        raw = bytes(_codec("zstd_decode")(payload))
    else:
        name = COMPRESSION_NAMES.get(compression, f"method {compression}")
        raise CziReadError(
            f"This CZI stores its tiles as {name}, which this reader cannot "
            f"decode. Re-export it from ZEN uncompressed."
        )

    array = np.frombuffer(raw, dtype=dtype)
    expected = width * height
    if array.size < expected:
        raise CziReadError(
            f"A tile holds {array.size} pixel(s) where its own dimensions call "
            f"for {expected} ({width}×{height}). The file is truncated or its "
            f"index is wrong."
        )
    return array[:expected].reshape(height, width)


def _shaped(plane: np.ndarray, width: int, height: int) -> np.ndarray:
    """*plane*, checked against the shape its directory entry promised."""
    if plane.shape != (height, width):
        raise CziReadError(
            f"A tile decoded to {plane.shape[1]}×{plane.shape[0]} where its "
            f"directory entry says {width}×{height}. The file's index does not "
            f"describe its pixels."
        )
    return plane


class CziReader:
    """An open CZI, indexed by ``(scene, tile, timepoint, channel, Z)``.

    Built once per reading thread — the file handle is not shared, for the same
    reason the OME exploder gives each worker its own ``TiffFile``: the seeks
    are the whole cost of a read, and serialising them behind a lock would undo
    the per-well parallelism the exploder exists to get.

    Used as a context manager::

        with CziReader(path) as reader:
            plane = reader.read_projection(scene, tile, t, c)
    """

    def __init__(self, path: Path | str, layout: CziLayout | None = None) -> None:
        self._path = Path(path)
        self._fh: BinaryIO | None = None
        self._layout = layout if layout is not None else read_czi_layout(self._path)
        self._index: dict[tuple[int, int, int, int, int], SubBlock] = {}
        #: ``(scene, tile, t, c)`` → the Z indices that coordinate really has,
        #: ascending. Read rather than assumed: asking for ``range(size_z)``
        #: raises on the shallower channel of a file whose stack depths differ
        #: per channel, which is an ordinary acquisition rather than a fault.
        self._z_planes: dict[tuple[int, int, int, int], list[int]] = {}

    def __enter__(self) -> "CziReader":
        self._fh = open(self._path, "rb")
        directory_position, _metadata = _read_file_header(self._fh)
        blocks: list[SubBlock] = []
        if directory_position > 0:
            try:
                blocks = _read_subblock_directory(self._fh, directory_position)
            except CziReadError:
                blocks = []
        if not blocks:
            blocks, _walked = _walk_segments(self._fh)
        for block in blocks:
            if block.is_pyramid:
                continue
            key = (block.start("S"), block.start("M"), block.start("T"),
                   block.start("C"), block.start("Z"))
            # First wins. A duplicated coordinate is refused by
            # ``validation_report`` before any archive is written, so reaching
            # here with one means the caller chose to go ahead anyway; taking
            # the first keeps the choice the same on every worker thread rather
            # than varying with directory order.
            self._index.setdefault(key, block)
        for scene, tile, t, c, z in sorted(self._index):
            self._z_planes.setdefault((scene, tile, t, c), []).append(z)
        return self

    def __exit__(self, *exc_info) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    @property
    def layout(self) -> CziLayout:
        return self._layout

    def has_plane(self, scene: int, tile: int, t: int, c: int, z: int) -> bool:
        return (scene, tile, t, c, z) in self._index

    def read_plane(self, scene: int, tile: int, t: int, c: int,
                   z: int) -> np.ndarray:
        """The 2-D image stored at these coordinates.

        Raises :class:`CziReadError` when the file holds no tile there — never
        a blank array, which would be indistinguishable from a real image of a
        dark field.
        """
        if self._fh is None:
            raise CziReadError("The CZI is not open; use CziReader as a "
                               "context manager.")
        block = self._index.get((scene, tile, t, c, z))
        if block is None:
            raise CziReadError(
                f"{self._path.name} holds no image for scene {scene}, tile "
                f"{tile}, timepoint {t}, channel {c}, Z {z}."
            )
        dtype = PIXEL_DTYPES.get(block.pixel_type)
        if dtype is None:
            raise CziReadError(
                f"{self._path.name} stores pixel type {block.pixel_type}, "
                f"which is not a single-intensity greyscale format."
            )
        width, height = block.size("X"), block.size("Y")
        data = self._read_block_data(block)
        return _decode_tile(data, block, width=width, height=height, dtype=dtype)

    def _read_block_data(self, block: SubBlock) -> bytes:
        """The raw (still compressed) bytes of one subblock's image data."""
        fh = self._fh
        assert fh is not None  # guarded by read_plane
        fh.seek(block.file_position)
        sid, _allocated, _used = _SEGMENT_HEADER.unpack(
            _read_exactly(fh, _SEGMENT_HEADER.size, "subblock"))
        if _token(sid) != "ZISRAWSUBBLOCK":
            raise CziReadError(
                f"The subblock directory points at a {_token(sid)!r} segment "
                f"where an image tile should be. The file is damaged."
            )
        body_start = fh.tell()
        metadata_size, _attachment_size, data_size = _SUBBLOCK_PROLOGUE.unpack(
            _read_exactly(fh, _SUBBLOCK_PROLOGUE.size, "subblock"))
        # The prologue and the repeated directory entry are padded out to at
        # least 256 bytes together, so the offset of the data depends on how
        # many dimensions this particular tile declares.
        entry_size = _DIRECTORY_ENTRY.size + _DIMENSION_ENTRY.size * len(block.dims)
        header_len = max(_SUBBLOCK_MIN_HEADER,
                         _SUBBLOCK_PROLOGUE.size + entry_size)
        fh.seek(body_start + header_len + metadata_size)
        if data_size < 0:
            raise CziReadError("A subblock declares a negative data size; the "
                               "file is damaged.")
        return _read_exactly(fh, data_size, "image tile")

    def read_projection(self, scene: int, tile: int, t: int, c: int) -> np.ndarray:
        """The Z stack at these coordinates, collapsed by maximum intensity.

        The standard reduction for fluorescence quantification, and the same one
        :mod:`pipeline.ome_explode` applies — so a plate converted from a CZI
        and one converted from an OME-TIFF are measuring the same thing.

        Projects the Z planes this coordinate *has*, which need not be every
        one the file's deepest stack has: a fluorescent channel imaged as a
        stack beside a single brightfield plane is routine, and both project
        correctly.
        """
        depths = self._z_planes.get((scene, tile, t, c), [])
        if not depths:
            raise CziReadError(
                f"{self._path.name} holds no image for scene {scene}, tile "
                f"{tile}, timepoint {t}, channel {c}."
            )
        planes = [self.read_plane(scene, tile, t, c, z) for z in depths]
        if len(planes) == 1:
            return planes[0]
        stack = np.stack(planes, axis=0)
        return stack.max(axis=0)
