"""Raw member-to-member copying between zip archives.

Every Datasets tool that re-packs a well container — Subset's filter,
Concatenate's timepoint rewrite, Merge plates' well-token remap — changes only
which members land in the destination and what they are *called*. None of them
changes a single byte of image data. Copying such a member by decompressing it
and compressing it again is therefore pure waste: it burns CPU proportional to
the uncompressed size of the dataset to reproduce bytes that already exist, and
deflate is by far the more expensive of the two halves.

This module copies a member's **already-compressed** bytes straight from one
archive into another. The destination member keeps the source's compression
method, CRC, and both sizes, so it decompresses to exactly the same data; only
its name and its position in the file change. Renaming is free for the same
reason — the name lives in the headers, not in the compressed payload.

**This depends on four ``zipfile`` implementation details** — ``ZipFile.fp``,
``ZipFile.filelist``, ``ZipFile.NameToInfo`` and ``ZipFile.start_dir`` (plus
the ``_didModify`` flag) — because the standard library exposes no public API
for adding a member whose bytes are already compressed. They have been stable
across every 3.x release and are what every third-party raw-zip-copy
implementation uses, but they are private, so:

* :func:`member_is_raw_copyable` gates every member before this module touches
  it, and a member it rejects (a directory entry, an encrypted member) must be
  copied by the caller through the ordinary decompress/recompress path.
* :class:`RawCopyUnsupported` is raised, with the destination left byte-exact
  as it was before the member, when an archive's local headers disagree with
  its central directory. That is a corrupt source, not a slow path — it is
  raised rather than worked around, because the ordinary path would fail on
  the same member for the same reason and quietly writing less than was asked
  for is the one outcome that must never happen here.
"""

from __future__ import annotations

import contextlib
import struct
import zipfile

__all__ = ["RawCopyUnsupported", "copy_member_raw", "member_is_raw_copyable"]

# Streamed in chunks rather than read whole: a member is one microscopy frame,
# but nothing about this code should assume that, and the whole point of the
# module is to stop holding a well's worth of pixels in memory at once.
_CHUNK = 1 << 20

_LOCAL_HEADER_SIZE = 30
_LOCAL_HEADER_SIGNATURE = b"PK\x03\x04"
# Offsets 26:30 of the local file header are the name and extra-field lengths;
# the member's data starts immediately after both.
_LOCAL_NAME_EXTRA_LENS = slice(26, 30)

_FLAG_ENCRYPTED = 0x1
_FLAG_DATA_DESCRIPTOR = 0x8

_ZIP64_EXTRA_ID = 1
# Info-ZIP's Unicode Path / Unicode Comment records. Each embeds a *copy* of
# the member's name together with a CRC of the name it was computed against,
# so a record carried across a rename describes the member's old name.
_UNICODE_PATH_EXTRA_ID = 0x7075
_UNICODE_COMMENT_EXTRA_ID = 0x6375


class RawCopyUnsupported(Exception):
    """A member could not be copied in compressed form. The source is at fault.

    Raised only after the destination has been restored to its exact
    pre-member state, so the caller may report the failure without having to
    treat the destination archive as damaged.
    """


def member_is_raw_copyable(info: zipfile.ZipInfo) -> bool:
    """Whether *info* is a member this module is entitled to copy.

    Directory entries carry no data, and an encrypted member's bytes cannot be
    republished under a fresh header without its password. Both are the
    caller's business, not this module's — hence a predicate rather than an
    exception, so a caller can route them to the ordinary path without
    treating an expected case as an error.
    """
    if info.is_dir():
        return False
    return not info.flag_bits & _FLAG_ENCRYPTED


def _filtered_extra(extra: bytes, drop_ids: frozenset[int]) -> bytes:
    """Drop the records in *drop_ids* from an extra field, keeping the rest.

    ZIP64 (id 1) always goes: ``ZipInfo.FileHeader`` appends a freshly-computed
    one when the member needs it, so carrying the source's — which describes
    the member's sizes at its *old* offsets — would leave two contradictory
    records in the same header.

    The Unicode Path and Comment records go too, but only when the member is
    being **renamed**. Each carries its own copy of the name plus a CRC of the
    name it was derived from, so across a rename it is a second, stale name
    for the same member: a reader that honours it and skips the CRC check
    restores the file under its pre-rename name, silently undoing the well
    remap or timepoint offset that was the entire point of the copy. A member
    keeping its own name keeps its records, which still describe it correctly.

    Everything else (timestamps, Unix ownership) describes the member itself
    and is copied through.
    """
    kept = bytearray()
    index = 0
    while index + 4 <= len(extra):
        header_id, size = struct.unpack("<HH", extra[index:index + 4])
        end = index + 4 + size
        if end > len(extra):
            # A record whose declared length runs past the buffer: the rest of
            # the field cannot be parsed, so it is dropped rather than emitted
            # as a header a reader would misparse.
            break
        if header_id not in drop_ids:
            kept += extra[index:end]
        index = end
    return bytes(kept)


def _drop_ids(source_name: str, dest_name: str) -> frozenset[int]:
    """Which extra-field records must not survive into the copied member."""
    if source_name == dest_name:
        return frozenset({_ZIP64_EXTRA_ID})
    return frozenset({
        _ZIP64_EXTRA_ID, _UNICODE_PATH_EXTRA_ID, _UNICODE_COMMENT_EXTRA_ID,
    })


def _dest_info(info: zipfile.ZipInfo, dest_name: str, header_offset: int) -> zipfile.ZipInfo:
    """The destination member's header, describing *info*'s bytes under a new name."""
    out = zipfile.ZipInfo(dest_name, info.date_time)
    out.compress_type = info.compress_type
    out.create_system = info.create_system
    out.create_version = info.create_version
    out.extract_version = info.extract_version
    out.external_attr = info.external_attr
    out.internal_attr = info.internal_attr
    out.comment = info.comment
    out.extra = _filtered_extra(info.extra, _drop_ids(info.filename, dest_name))
    out.CRC = info.CRC
    out.compress_size = info.compress_size
    out.file_size = info.file_size
    # The central directory is authoritative for CRC and sizes even when the
    # source wrote them in a trailing data descriptor, and this header carries
    # them inline — so the descriptor flag must come off, or a reader would
    # look for a trailer that isn't there.
    out.flag_bits = info.flag_bits & ~_FLAG_DATA_DESCRIPTOR
    out.header_offset = header_offset
    return out


def _seek_to_member_data(src: zipfile.ZipFile, info: zipfile.ZipInfo) -> None:
    """Position *src*'s handle at the first byte of *info*'s compressed data."""
    handle = src.fp
    if handle is None:
        raise RawCopyUnsupported(f"{info.filename}: the source archive is closed")
    handle.seek(info.header_offset)
    header = handle.read(_LOCAL_HEADER_SIZE)
    if len(header) != _LOCAL_HEADER_SIZE or not header.startswith(_LOCAL_HEADER_SIGNATURE):
        raise RawCopyUnsupported(
            f"{info.filename}: no local file header at offset {info.header_offset} "
            "— the archive's central directory disagrees with its contents"
        )
    name_len, extra_len = struct.unpack("<HH", header[_LOCAL_NAME_EXTRA_LENS])
    handle.seek(info.header_offset + _LOCAL_HEADER_SIZE + name_len + extra_len)


def copy_member_raw(
    src: zipfile.ZipFile, info: zipfile.ZipInfo,
    dst: zipfile.ZipFile, dest_name: str,
) -> zipfile.ZipInfo:
    """Copy *info* from *src* into *dst* as *dest_name*, without recompressing.

    *dst* must be a write-mode archive on a seekable file. *info* must have
    passed :func:`member_is_raw_copyable`. Returns the destination's
    :class:`~zipfile.ZipInfo`.

    On :class:`RawCopyUnsupported` the destination file is truncated back to
    where it stood before the call, so a failure part-way through a member
    cannot leave an unparseable run of bytes between two good records.
    """
    _seek_to_member_data(src, info)

    resume = dst.fp.tell()
    out = _dest_info(info, dest_name, resume)
    zip64 = (
        info.file_size > zipfile.ZIP64_LIMIT
        or info.compress_size > zipfile.ZIP64_LIMIT
    )
    try:
        dst.fp.write(out.FileHeader(zip64))
        remaining = info.compress_size
        while remaining > 0:
            chunk = src.fp.read(min(_CHUNK, remaining))
            if not chunk:
                raise RawCopyUnsupported(
                    f"{info.filename}: the archive ends {remaining} byte(s) before "
                    f"the member's declared {info.compress_size} — it is truncated"
                )
            dst.fp.write(chunk)
            remaining -= len(chunk)
    except Exception:
        # Suppressed because the rollback is a courtesy on a failure path: a
        # destination handle that cannot be truncated would otherwise replace
        # the real error with its own, and the caller needs the first one.
        with contextlib.suppress(Exception):
            dst.fp.seek(resume)
            dst.fp.truncate()
        raise

    dst.filelist.append(out)
    dst.NameToInfo[out.filename] = out
    dst._didModify = True
    # Where the central directory will start; close() seeks here to write it.
    dst.start_dir = dst.fp.tell()
    return out
