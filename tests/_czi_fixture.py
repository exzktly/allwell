"""Write a real ZISRAW (.czi) file, so the CZI reader can be tested on bytes.

There is no CZI in this repository and no library that writes one, so a test
that wants to know whether ``pipeline.czi_read`` reads a plate scan correctly
has two options: mock the parser, which tests nothing, or lay the bytes down by
hand. This is the second. It writes the four segment kinds the reader
understands — file header, subblock, metadata, subblock directory — from the
same struct definitions the reader uses, so a change to either side that breaks
the agreement between them shows up as a failing round-trip rather than as two
modules quietly disagreeing about a field width.

Deliberately *not* a general CZI writer. It emits exactly what a Zeiss plate
scan emits and what the reader claims to handle, plus the specific damage the
reader's refusals are about (a pyramid copy, a missing tile, a duplicated
coordinate), because a fixture that can only produce well-formed files can only
ever confirm the happy path (CLAUDE.md §7.1 #4).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pipeline.czi_read import (
    _DIMENSION_ENTRY,
    _DIRECTORY_ENTRY,
    _DIRECTORY_HEADER_LEN,
    _FILE_HEADER,
    _METADATA_HEADER_LEN,
    _SEGMENT_HEADER,
    _SUBBLOCK_MIN_HEADER,
    _SUBBLOCK_PROLOGUE,
)

#: numpy dtype name → the CZI ``PixelType`` that denotes it.
PIXEL_TYPE_FOR = {"uint8": 0, "uint16": 1, "float32": 2, "int32": 12,
                  "float64": 13}


@dataclass
class Tile:
    """One 2-D image and the coordinates it sits at."""

    pixels: np.ndarray
    scene: int = 0
    tile: int = 0
    t: int = 0
    c: int = 0
    z: int = 0
    #: Extra axes to declare on this tile, as ``{axis: (start, size)}`` — for
    #: the tests that check an axis this importer has no filename field for.
    extra: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: Emit this tile as a downsampled pyramid copy rather than an acquisition
    #: tile: the reader must discard it. ``True`` sets both of the tells a CZI
    #: can carry; ``"type"`` sets only the ``PyramidType`` field and
    #: ``"stored"`` only the reduced ``StoredSize``, because a writer may emit
    #: either on its own and a reader that needs both would keep the copies.
    pyramid: bool | str = False
    compression: int = 0
    #: Claim this tile's pixels live in a numbered companion file, as ZEN does
    #: when it splits a very large acquisition into parts.
    file_part: int = 0
    #: The wall-clock instant to stamp into this tile's *own* metadata, as ZEN
    #: does — an ISO 8601 string written verbatim, so a test can emit the
    #: seven-digit fractional second real files carry as easily as a malformed
    #: one. ``None`` writes the bare ``<METADATA/>`` a tile with no timing
    #: carries, which is the fixture's default and the positional-token case.
    acquired: "str | None" = None


def _segment(name: str, data: bytes) -> bytes:
    """One segment: its 32-byte header followed by *data*, nothing padded."""
    return _SEGMENT_HEADER.pack(name.encode("ascii"), len(data), len(data)) + data


def _directory_entry(tile: Tile, *, position: int) -> bytes:
    """The ``DirectoryEntryDV`` describing *tile*, pointing at *position*."""
    height, width = tile.pixels.shape
    stored_reduced = tile.pyramid in (True, "stored")
    type_set = tile.pyramid in (True, "type")
    dims: list[tuple[str, int, int, int]] = [
        ("X", 0, width, width // 2 if stored_reduced else width),
        ("Y", 0, height, height // 2 if stored_reduced else height),
    ]
    dims.append(("C", tile.c, 1, 1))
    dims.append(("T", tile.t, 1, 1))
    dims.append(("Z", tile.z, 1, 1))
    dims.append(("S", tile.scene, 1, 1))
    dims.append(("M", tile.tile, 1, 1))
    for axis, (start, size) in sorted(tile.extra.items()):
        dims.append((axis, start, size, size))
    pixel_type = PIXEL_TYPE_FOR[str(tile.pixels.dtype)]
    out = _DIRECTORY_ENTRY.pack(
        b"DV", pixel_type, position, tile.file_part, tile.compression,
        1 if type_set else 0, 0, b"\0\0\0\0", len(dims),
    )
    for axis, start, size, stored in dims:
        out += _DIMENSION_ENTRY.pack(
            axis.encode("ascii").ljust(4, b"\0"), start, size, float(start), stored)
    return out


def _subblock_metadata(tile: Tile) -> bytes:
    """The per-tile metadata block, with the tags ZEN puts in it.

    A tile with no ``acquired`` gets the bare ``<METADATA/>`` — the shape a
    reader has to fall back from. One with it gets the surrounding tags too,
    because a scanner that found the timestamp only when it was the sole
    element would find nothing in a real file.
    """
    if tile.acquired is None:
        return b"<METADATA/>"
    return (
        "<METADATA><Tags>"
        f"<AcquisitionTime>{tile.acquired}</AcquisitionTime>"
        f"<StageXPosition>{tile.scene * 9000.0:.4f}</StageXPosition>"
        f"<StageYPosition>{tile.tile * 700.0:.4f}</StageYPosition>"
        "<FocusPosition>-12.3456</FocusPosition>"
        "</Tags><DataSchema><ValidBitsPerPixel>16</ValidBitsPerPixel>"
        "</DataSchema><AttachmentSchema/></METADATA>"
    ).encode("utf-8")


def _subblock_segment(tile: Tile, *, position: int) -> bytes:
    """A whole ``ZISRAWSUBBLOCK`` segment holding *tile*'s pixels."""
    entry = _directory_entry(tile, position=position)
    pixels = tile.pixels.tobytes()
    metadata = _subblock_metadata(tile)
    header = _SUBBLOCK_PROLOGUE.pack(len(metadata), 0, len(pixels)) + entry
    pad = max(_SUBBLOCK_MIN_HEADER, len(header)) - len(header)
    return _segment("ZISRAWSUBBLOCK", header + b"\0" * pad + metadata + pixels)


def _metadata_segment(xml: str) -> bytes:
    raw = xml.encode("utf-8")
    head = struct.pack("<ii", len(raw), 0)
    head += b"\0" * (_METADATA_HEADER_LEN - len(head))
    return _segment("ZISRAWMETADATA", head + raw)


def plate_metadata_xml(
    *,
    scene_wells: dict[int, str] | None = None,
    channels: list[str] | None = None,
    scene_name_only: bool = False,
) -> str:
    """The metadata a Zeiss plate scan carries: named scenes and named channels.

    With *scene_name_only* the wells are written as the scene's ``Name``
    attribute rather than as a ``<Shape>``, which is the other spelling seen in
    the wild and the one :func:`pipeline.czi_read.well_from_scene_name` has to
    dig a well out of.
    """
    channel_xml = "".join(
        f'<Channel Id="Channel:{i}" Name="{name}"/>'
        for i, name in enumerate(channels or [])
    )
    scene_xml = ""
    for index, well in sorted((scene_wells or {}).items()):
        if scene_name_only:
            scene_xml += f'<Scene Index="{index}" Name="P1-{well}"></Scene>'
        else:
            scene_xml += (f'<Scene Index="{index}" Name="Scene-{index + 1}">'
                          f'<Shape Name="{well}"/></Scene>')
    return (
        "<ImageDocument><Metadata><Information><Image>"
        f"<Dimensions><Channels>{channel_xml}</Channels>"
        f"<S><Scenes>{scene_xml}</Scenes></S></Dimensions>"
        "</Image></Information></Metadata></ImageDocument>"
    )


def write_czi(path: Path | str, tiles: list[Tile], *, xml: str = "",
              omit_directory: bool = False) -> Path:
    """Write *tiles* as a CZI at *path* and return it.

    With *omit_directory* the subblock directory is left out and the file
    header's pointer to it zeroed — the state an acquisition killed before it
    finished writing leaves behind, which the reader recovers from by walking
    the segments.
    """
    path = Path(path)
    header_data = bytearray(512)
    file_segment = _SEGMENT_HEADER.pack(b"ZISRAWFILE", len(header_data),
                                        len(header_data))
    position = len(file_segment) + len(header_data)

    body = bytearray()
    entries: list[bytes] = []
    for tile in tiles:
        segment = _subblock_segment(tile, position=position)
        entries.append(_directory_entry(tile, position=position))
        body += segment
        position += len(segment)

    metadata_position = position if xml else 0
    if xml:
        metadata = _metadata_segment(xml)
        body += metadata
        position += len(metadata)

    directory_position = 0
    if not omit_directory:
        directory_position = position
        payload = struct.pack("<i", len(entries))
        payload += b"\0" * (_DIRECTORY_HEADER_LEN - len(payload))
        payload += b"".join(entries)
        body += _segment("ZISRAWDIRECTORY", payload)

    _FILE_HEADER.pack_into(
        header_data, 0, 1, 0, 0, 0, b"\0" * 16, b"\0" * 16, 0,
        directory_position, metadata_position, 0, 0,
    )
    path.write_bytes(file_segment + bytes(header_data) + bytes(body))
    return path


def plate_tiles(
    wells: int = 2, fovs: int = 1, timepoints: int = 1, channels: int = 2,
    z: int = 1, shape: tuple[int, int] = (4, 6), dtype: str = "uint16",
) -> list[Tile]:
    """A full rectangular acquisition, each plane filled with its own value.

    The value is derived from every coordinate, so a plane written to the wrong
    filename is a *different number*, not merely a different-looking image —
    which is what lets a test assert the mapping rather than the pixel count.
    """
    out: list[Tile] = []
    height, width = shape
    for s in range(wells):
        for m in range(fovs):
            for t in range(timepoints):
                for c in range(channels):
                    for zi in range(z):
                        value = (((s * 10 + m) * 10 + t) * 10 + c) * 10 + zi
                        pixels = np.full((height, width), value, dtype=dtype)
                        out.append(Tile(pixels=pixels, scene=s, tile=m, t=t,
                                        c=c, z=zi))
    return out
