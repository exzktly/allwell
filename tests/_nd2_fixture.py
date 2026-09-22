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

def plate_value(pos: int, t: int, c: int) -> int:
    """The value every pixel of one plane carries.

    Derived from all three coordinates, so a plane read or written to the
    wrong place is a *different number* rather than a different-looking image
    — which is what lets a test assert the mapping rather than the pixel count.
    """
    return (pos * 10 + t) * 10 + c


def write_nd2(path, *, n_pos=2, n_t=2, n_c=1, width=8, height=8,
              dtype="uint16", well_names=None, channel_names=None,
              times_ms=None, drop_probe=False):
    """Write a plate scan at *path* and return it.

    *times_ms* gives the elapsed milliseconds for each timepoint, written into
    every frame's own metadata the way NIS writes them; ``None`` writes none at
    all, which is the case the converter must fall back from. *drop_probe*
    omits the fluorescent-probe block, which is the shape that makes ``nd2``
    0.9.0's ``metadata`` raise.
    """
    path = Path(path)
    n_seq = n_pos * n_t
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
        pt = {"dPosX": 100.0 * i, "dPosY": 0.0, "dPosZ": 0.0, "dPFSOffset": -1.0}
        if i < len(names):
            pt["dPosName"] = names[i]
        points[f"a{i}"] = pt
    # Positions on the outside, a time loop nested under them — the shape a
    # plate time-course has. ``ppNextLevelEx`` is how ND2 nests its loops, and
    # each value is an experiment dict in its own right.
    experiment = {
        "eType": 2,
        "uLoopPars": {"uiCount": n_pos, "Points": points, "bUseZ": False,
                      "bRelativeXY": False},
        "ppNextLevelEx": {"a0": {
            "eType": 1,
            "uLoopPars": {"uiCount": n_t, "dStart": 0.0,
                          "dPeriod": 3600_000.0,
                          "dDuration": 3600_000.0 * n_t},
        }},
    }
    put(b"ImageMetadataLV!", clx({"SLxExperiment": experiment}))
    put(b"ImageTextInfoLV!", clx({"SLxImageTextInfo": {"TextInfoItem_5": "fixture"}}))

    chan_names = list(channel_names or [f"C{i}" for i in range(n_c)])
    for seq in range(n_seq):
        pos, t = divmod(seq, n_t)
        planes = {f"a{i}": _plane_meta(chan_names[i], i, drop_probe=drop_probe)
                  for i in range(n_c)}
        meta = {"SLxPictureMetadata": {
            "sPicturePlanes": {"sPlaneNew": planes, "uiCount": n_c,
                               "uiSampleCount": n_c},
            "dTimeMSec": 0.0 if times_ms is None else float(times_ms[t]),
            "dZPos": 0.0,
            # Read unconditionally by ``load_global_metadata`` — these sit on
            # the picture-metadata level, not on each plane.
            "bCalibrated": False,
            "dCalibration": 0.0,
        }}
        put(f"ImageMetadataSeqLV|{seq}!".encode(), clx(meta))

    for seq in range(n_seq):
        pos, t = divmod(seq, n_t)
        # One interleaved plane per sequence: the channels sit side by side
        # along the row, which is what ``uiWidthBytes`` describes.
        rows = np.zeros((height, width * comp), dtype=dtype)
        for c in range(n_c):
            rows[:, c::comp] = plate_value(pos, t, c)
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
        stamps = np.array([float(times_ms[seq % n_t]) for seq in range(n_seq)],
                          dtype="<f8")
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
