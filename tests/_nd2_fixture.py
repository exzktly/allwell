"""Write a real Nikon ND2 file, so the ND2 reader can be tested on bytes.

There is no ``.nd2`` in this repository and the ``nd2`` library ships no
writer, so a test that wants to know whether ``pipeline.nd2_read`` reads a
plate scan correctly has two options: stub ``nd2.ND2File``, which tests
nothing but that this module's own assumptions agree with themselves, or lay
the bytes down by hand. This is the second, and here it is worth more than it
was for the CZI fixture: the parser on the other side is **not ours**. A file
this module writes and ``nd2.ND2File`` reads back is evidence about the
format, not a stub agreeing with the code that built it (CLAUDE.md §7.8).

The format, all of it taken from the reader's own source rather than guessed:

* Every chunk is ``magic(0x0ABECEDA) | nameLen | dataLen`` then the name then
  the data. The file opens with a 32-byte-name/64-byte-data signature chunk
  and closes with 40 bytes giving the chunk map's offset.
* Metadata blocks are **CLX-lite**: each record is ``type | nameLen`` then the
  name as UTF-16 (length in *characters*, including a trailing NUL) then the
  value. A level (type 11) carries ``itemCount | length`` where ``length``
  spans the record from its own start through the end of its nested payload,
  followed by an ``itemCount * 8`` offset table the reader skips.
* A list is a level whose members all carry the empty name.

Deliberately **not** a general ND2 writer. It emits what a Nikon plate scan
emits and what the converter claims to handle, plus the specific damage each
refusal is about, because a fixture that can only produce well-formed files
can only ever confirm the happy path (CLAUDE.md §7.1 #4).

One piece of that mess is load-bearing and worth naming. A real NIS file gives
every plane a fluorescent-probe block, and ``nd2`` 0.9.0 does not merely
prefer one: ``_get_excitation`` binds its ``excitation`` local only inside
``if probe:`` and then reads it unconditionally, so a plane without a probe
raises ``UnboundLocalError`` from ``ND2File.metadata``. The fixture writes the
block; :mod:`pipeline.nd2_read` additionally guards the call, because a user's
file is not obliged to carry one.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

CHUNK_MAGIC = 0x0ABECEDA
CHUNK_HEADER = struct.Struct("IIQ")
FILE_SIG = b"ND2 FILE SIGNATURE CHUNK NAME01!"      # 31 + NUL = 32
CHUNKMAP_SIG = b"ND2 CHUNK MAP SIGNATURE 0000001!"
FILEMAP_SIG = b"ND2 FILEMAP SIGNATURE NAME 0001!"
QQ = struct.Struct("QQ")
#: Where a frame's pixels sit, measured from its chunk's own start.
_FRAME_DATA_OFFSET = 4096

#: ``nd2.structures.LoopType`` codes for the three axes a plate scan varies.
#: Named here rather than imported so the fixture writes the number the format
#: uses and the reader is left to parse it, which is the whole point of laying
#: the bytes down by hand.
_LOOP_TYPE = {"P": 2, "T": 1, "Z": 4}       # XYPosLoop, TimeLoop, ZStackLoop

# ── CLX-lite encoding ────────────────────────────────────────────────────────
BOOL, INT32, UINT32, INT64, UINT64, DOUBLE, VOIDP, STRING, BYTEARRAY, LEVEL = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 11)

def _name(text: str) -> bytes:
    return (text + "\0").encode("utf-16-le")

def _record(name: str, dtype: int, payload: bytes) -> bytes:
    nb = _name(name)
    return struct.pack("BB", dtype, len(nb) // 2) + nb + payload

def enc(name: str, value) -> bytes:
    if isinstance(value, bool):
        return _record(name, BOOL, struct.pack("B", 1 if value else 0))
    if isinstance(value, int):
        return _record(name, UINT32, struct.pack("<I", value)) if 0 <= value < 2**32 \
            else _record(name, INT64, struct.pack("<q", value))
    if isinstance(value, float):
        return _record(name, DOUBLE, struct.pack("<d", value))
    if isinstance(value, str):
        return _record(name, STRING, (value + "\0").encode("utf-16-le"))
    if isinstance(value, dict):
        return _level(name, list(value.items()))
    if isinstance(value, (list, tuple)):
        # nd2 spells a list as a level whose members all have the empty name
        return _level(name, [("", v) for v in value])
    raise TypeError(f"cannot encode {name}={value!r}")

def _level(name: str, items) -> bytes:
    body = b"".join(enc(k, v) for k, v in items)
    nb = _name(name)
    head = struct.pack("BB", LEVEL, len(nb) // 2) + nb
    # `length` spans from the record start through the end of the nested body,
    # excluding the trailing offset table the reader skips.
    length = len(head) + struct.calcsize("<IQ") + len(body)
    return head + struct.pack("<IQ", len(items), length) + body + b"\0" * (len(items) * 8)

def clx(mapping: dict) -> bytes:
    return b"".join(enc(k, v) for k, v in mapping.items())

# ── chunk container ──────────────────────────────────────────────────────────
def chunk(name: bytes, data: bytes) -> bytes:
    return CHUNK_HEADER.pack(CHUNK_MAGIC, len(name), len(data)) + name + data

def plate_value(pos: int, t: int, c: int, z: int = 0) -> int:
    """The value every pixel of one plane carries.

    Derived from **all four** coordinates, so a plane read or written to the
    wrong place is a *different number* rather than a different-looking image
    — which is what lets a test assert the mapping rather than the pixel count.

    Z is in here because leaving it out is what hid a real defect. The suite
    could not write a Z stack at all, so every plane's value was a function of
    the three coordinates the code happened to handle, and a converter that
    silently dropped the Z axis produced exactly the numbers the tests
    expected. A coordinate the fixture cannot vary is a coordinate no
    assertion can measure.

    Z varies in the **units digit** and the others above it, so a plane taken
    from the wrong Z is a near-miss rather than a wildly different number —
    which is the shape a max-projection bug really has, and which an assertion
    on the exact value still catches.
    """
    return ((pos * 10 + t) * 10 + c) * 10 + z


def write_nd2(path, *, n_pos=2, n_t=2, n_c=1, n_z=1, width=8, height=8,
              dtype="uint16", well_names=None, channel_names=None,
              times_ms=None, drop_probe=False, loop_order="PT", stage_um=None,
              calibration_um=None):
    """Write a plate scan at *path* and return it.

    *times_ms* gives the elapsed milliseconds for each timepoint, written into
    every frame's own metadata the way NIS writes them; ``None`` writes none at
    all, which is the case the converter must fall back from. *drop_probe*
    omits the fluorescent-probe block, which is the shape that makes ``nd2``
    0.9.0's ``metadata`` raise.

    *loop_order* is the experiment's **loop nesting order**, outermost first,
    as a string over ``P`` (stage position), ``T`` (time) and ``Z``. It is the
    parameter this fixture most needed and did not have. ``nd2`` builds its
    sequence index from the loops in the file's own order, so a reader that
    assumes one order reads every plane from the wrong place on a file written
    in another — and the suite could previously write only ``"PT"``, which is
    the order the reader assumed, so no assertion could catch it.

    ``"PT"`` is a plate scanned position by position, each position's whole
    time series before moving on. ``"TP"`` is a multipoint time-course, where
    the stage visits every position within each round and comes back — which
    is the physically ordinary shape for a live plate, and the one the reader
    got wrong. An axis left out of *loop_order* is not written as a loop at
    all; an axis in it whose count is 1 still is, because a one-plane Z stack
    is a real file.

    *stage_um* gives each position loop point's ``(x, y)`` in µm (the default puts
    position *i* at ``(100 i, 0)``). *calibration_um* sets the pixel calibration and
    its ``bCalibrated`` flag; ``None`` writes an uncalibrated file, as the default
    always has.

    *n_z* is the Z-stack depth. Z is a **coordinate** axis in ND2, never a
    component of a frame — ``nd2``'s own ``AXIS.frame_coords()`` is
    ``{S, C, X, Y}`` — so a Z stack multiplies the sequence count and each
    plane is fetched by its own index. A converter is expected to
    max-project it.
    """
    path = Path(path)
    axes = [a for a in loop_order.upper() if a in _LOOP_TYPE]
    if not axes:
        raise ValueError(f"loop_order {loop_order!r} names no loop axis")
    counts = {"P": n_pos, "T": n_t, "Z": n_z}
    # The sequence index is a row-major ravel over the loop axes in *this*
    # order, which is exactly what ``nd2`` reconstructs from the experiment.
    shape = [counts[a] for a in axes]
    n_seq = 1
    for extent in shape:
        n_seq *= extent
    comp = n_c
    bpc = 16 if dtype == "uint16" else 8
    width_bytes = width * comp * (bpc // 8)

    attrs = {
        "uiWidth": width, "uiWidthBytes": width_bytes, "uiHeight": height,
        "uiComp": comp, "uiBpcInMemory": bpc, "uiBpcSignificant": bpc,
        "uiSequenceCount": n_seq, "uiTileWidth": width, "uiTileHeight": height,
        "eCompression": 2, "dCompressionParam": 0.0,
    }

    out = bytearray()
    out += CHUNK_HEADER.pack(CHUNK_MAGIC, 32, 64) + FILE_SIG + b"Ver3.0".ljust(64, b"\0")
    entries: list[tuple[bytes, int, int]] = []

    def put(name: bytes, data: bytes) -> None:
        entries.append((name, len(out), len(data)))
        out.extend(chunk(name, data))

    put(b"ImageAttributesLV!", clx({"SLxImageAttributes": attrs}))

    names = list(well_names or [])
    points = {}
    for i in range(n_pos):
        x, y = stage_um[i] if stage_um is not None else (100.0 * i, 0.0)
        pt = {"dPosX": float(x), "dPosY": float(y), "dPosZ": 0.0, "dPFSOffset": -1.0}
        if i < len(names):
            pt["dPosName"] = names[i]
        points[f"a{i}"] = pt

    def _loop(axis: str) -> dict:
        """One experiment level for *axis*, with the loop parameters nd2 parses."""
        if axis == "P":
            return {"eType": _LOOP_TYPE["P"],
                    "uLoopPars": {"uiCount": n_pos, "Points": points,
                                  "bUseZ": False, "bRelativeXY": False}}
        if axis == "T":
            return {"eType": _LOOP_TYPE["T"],
                    "uLoopPars": {"uiCount": n_t, "dStart": 0.0,
                                  "dPeriod": 3600_000.0,
                                  "dDuration": 3600_000.0 * n_t}}
        return {"eType": _LOOP_TYPE["Z"],
                "uLoopPars": {"uiCount": n_z, "dZLow": 0.0,
                              "dZHigh": float(max(0, n_z - 1)),
                              "dZStep": 1.0, "dZHome": 0.0,
                              "bZInverted": False, "iType": 0}}

    # ``ppNextLevelEx`` is how ND2 nests its loops, and each value is an
    # experiment dict in its own right. The nesting order is *the* thing this
    # parameter exists to vary: ``nd2`` builds its sequence index from the
    # loops in the file's own order, so a reader that assumes one order reads
    # every plane from the wrong place on a file written in another — and
    # until this fixture could write both, no assertion could say so.
    experiment = _loop(axes[0])
    node = experiment
    for axis in axes[1:]:
        child = _loop(axis)
        node["ppNextLevelEx"] = {"a0": child}
        node = child
    put(b"ImageMetadataLV!", clx({"SLxExperiment": experiment}))
    put(b"ImageTextInfoLV!", clx({"SLxImageTextInfo": {"TextInfoItem_5": "fixture"}}))

    def _coords(seq: int) -> dict:
        """``{'P': p, 'T': t, 'Z': z}`` for a sequence index, in *this* file's order."""
        out_coords = {"P": 0, "T": 0, "Z": 0}
        remainder = seq
        for axis, extent in zip(reversed(axes), reversed(shape)):
            remainder, out_coords[axis] = divmod(remainder, extent)
        return out_coords

    chan_names = list(channel_names or [f"C{i}" for i in range(n_c)])
    for seq in range(n_seq):
        coords = _coords(seq)
        pos, t = coords["P"], coords["T"]
        planes = {f"a{i}": _plane_meta(chan_names[i], i, drop_probe=drop_probe)
                  for i in range(n_c)}
        meta = {"SLxPictureMetadata": {
            "sPicturePlanes": {"sPlaneNew": planes, "uiCount": n_c,
                               "uiSampleCount": n_c},
            "dTimeMSec": 0.0 if times_ms is None else float(times_ms[t]),
            "dZPos": 0.0,
            # Read unconditionally by ``load_global_metadata`` — these sit on
            # the picture-metadata level, not on each plane.
            "bCalibrated": calibration_um is not None,
            "dCalibration": 0.0 if calibration_um is None else float(calibration_um),
        }}
        put(f"ImageMetadataSeqLV|{seq}!".encode(), clx(meta))

    for seq in range(n_seq):
        coords = _coords(seq)
        pos, t, z = coords["P"], coords["T"], coords["Z"]
        # One interleaved plane per sequence: the channels sit side by side
        # along the row, which is what ``uiWidthBytes`` describes.
        rows = np.zeros((height, width * comp), dtype=dtype)
        for c in range(n_c):
            rows[:, c::comp] = plate_value(pos, t, c, z)
        # The reader locates a frame's pixels at a *fixed* 4096 bytes from the
        # chunk's own start (``24 + 4072`` in its source), not straight after
        # the chunk header — so the header, the name and a pad have to fill
        # exactly that much. Writing the pixels immediately after the header
        # produced a file that opened, reported the right shape, and read every
        # plane as zeros.
        name = f"ImageDataSeq|{seq}!".encode()
        pad = _FRAME_DATA_OFFSET - CHUNK_HEADER.size - len(name)
        put(name, b"\0" * pad + rows.tobytes())

    if times_ms is not None:
        # Frame times come from this cache rather than from each frame's own
        # metadata: ``_cached_frame_times`` reads it as a flat array of
        # float64 milliseconds, one per sequence, and ``frame_metadata``
        # indexes straight into it.
        # One stamp per *sequence*, and the timepoint each sequence carries is
        # decoded through this file's own loop order. Keying it on ``seq % n_t``
        # was right only for a position-outer file and silently wrong for
        # every other — the same assumption the reader made.
        stamps = np.array([float(times_ms[_coords(seq)["T"]])
                           for seq in range(n_seq)], dtype="<f8")
        put(b"CustomData|AcqTimesCache!", stamps.tobytes())

    cm = bytearray()
    for name, off, size in entries:
        cm += name + QQ.pack(off, size)
    cm += CHUNKMAP_SIG
    cm_offset = len(out)
    out += chunk(FILEMAP_SIG, bytes(cm))
    out += struct.pack("32sQ", CHUNKMAP_SIG, cm_offset)
    path.write_bytes(bytes(out))
    return path


def _plane_meta(name: str, index: int, *, drop_probe: bool) -> dict:
    """One channel's plane block, with the probe a real NIS file writes."""
    plane = {
        "uiCompCount": 1, "sDescription": name, "uiColor": 0xFFFFFF,
        "dObjectiveMag": 20.0, "dZoom": 1.0, "dPinholeRadius": 0.0,
    }
    if not drop_probe:
        plane["pFluorescentProbe"] = {
            "m_ExcitationSpectrum": {"pPoint": {"Point0": {
                "dTValue": 1.0, "dWavelength": 405.0 + 100.0 * index,
                "eType": 0}}},
            "m_EmissionSpectrum": {"pPoint": {"Point0": {
                "dTValue": 1.0, "dWavelength": 460.0 + 100.0 * index,
                "eType": 0}}},
        }
    return plane
