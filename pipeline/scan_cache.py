"""Remember a plate's header scan on disk, so reopening it costs no parsing.

Reading a CZI's header means walking its subblock directory entry by entry and
parsing its plate metadata document with ``ElementTree`` — about a megabyte of
directory and tens of megabytes of XML per file, none of it pixels. On a
660-file split-scene export held on a network share that is the better part of
an hour, and the app does it again the next time the folder is opened, for
files that have not moved.

:mod:`pipeline.czi_read` already remembers both answers **in memory**, keyed on
``(path, size, mtime_ns)``, which is what makes the four passes a plate is read
in cost one read. This module is that same cache written to a file, so the
memory survives the process. Nothing else changes: :func:`load` puts what the
file holds back into ``czi_read``'s caches before a scan starts, the scan's
existing lookups hit them, and :func:`save` writes back what the scan learned.

**The key is the same key, and that is the point.** A reuse decision keyed on
less than everything that makes the old answer wrong is how a stale number gets
reported as a fresh one (CLAUDE.md §7.7 #3), so an entry is used only when the
file's size and modification time in nanoseconds both still match, and only
when it was written by this :data:`PARSER_VERSION`. The version is the half a
file stamp cannot cover: what is being reused is not the file, it is
``read_czi_layout``'s *output*, which is a function of the file **and** of the
reader that parsed it. Without it, a fix to the parser would go on serving the
answers the broken one gave, from every user's disk, with nothing saying so.
``tests/test_the_scan_is_remembered_on_disk.py`` pins it against a hash of the
reader's own source, because "remember to bump the constant" is exactly the
instruction that is not followed.

**It never writes into the images' own folder.** A plate is routinely on a
read-only archive, a share somebody else administers, or a cloud-sync root, and
a converter that drops files into raw acquisition data is a converter people
stop pointing at raw acquisition data. The cache lives beside the boot log in
the user's own cache directory, one file per scanned folder, and losing it
costs exactly one slow scan.

Stdlib only, like the rest of ``pipeline/`` — see ARCHITECTURE §6.2.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from pipeline import czi_read
from pipeline.timepoints import TimeScan
from util.aw_safe import swallowed

#: Bump when :func:`pipeline.czi_read.read_czi_layout` or
#: :func:`~pipeline.czi_read.parse_plate_metadata` could produce a different
#: answer from the same bytes. Every cached entry on every user's disk was
#: produced by the old one and the file stamp cannot know that.
PARSER_VERSION = 2

#: The on-disk shape itself, as distinct from the reader that filled it. Bumped
#: when a field is added, removed or re-spelled, so an old file is dropped
#: rather than half-read.
FORMAT_VERSION = 2

#: ``ALLWELL_SCAN_CACHE=off`` disables it; any other value is a directory to
#: use instead of the default. For a machine where the cache directory is not
#: writable, and for measuring a cold scan without deleting anything.
_OVERRIDE = "ALLWELL_SCAN_CACHE"

#: How many folders' caches to keep. Each is a few hundred kilobytes for a
#: 96-file plate, and the oldest go first — a bound rather than a policy, so
#: that a directory nobody looks at cannot grow without end.
_MAX_FOLDERS = 64

#: The dataclasses an entry may contain, by name. A decoder that would build
#: anything it is asked for is a decoder that builds whatever a corrupt or
#: hostile file names, so the roster is explicit and short.
_DECODABLE = {
    "CziLayout": czi_read.CziLayout,
    "TimeScan": TimeScan,
    "_PlateMetadata": czi_read._PlateMetadata,
}

_LOCK = threading.Lock()


# ── where it lives ──────────────────────────────────────────────────────────

def cache_dir() -> "Path | None":
    """The directory the scan caches live in, or ``None`` when disabled.

    ``czi-scans`` inside the first usable folder of
    :func:`util.state_dir.candidate_dirs` (``~/.cache/allwell`` first) — the
    pipeline's folder, which the desktop app's logs and caches no longer share
    (they are in ``util.state_dir.gui_state_dir``). ``None`` rather than an exception when nothing is writable:
    a scan that cannot be remembered is a slow scan, not a failed one.
    """
    raw = os.environ.get(_OVERRIDE, "").strip()
    if raw.lower() in {"off", "0", "false", "no"}:
        return None
    if raw:
        return _usable(Path(raw).expanduser())
    from util.state_dir import candidate_dirs
    for base in candidate_dirs():
        found = _usable(base / "czi-scans")
        if found is not None:
            return found
    return None


def _usable(folder: Path) -> "Path | None":
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        swallowed(f"scan cache directory {folder}")
        return None
    return folder if os.access(folder, os.W_OK) else None


def cache_file_for(folder: Path) -> "Path | None":
    """Where *folder*'s scan is remembered, or ``None`` when it cannot be.

    Named for the folder and *hashed on its absolute path*, so two plates
    called ``split`` under different parents do not share an entry — the name
    is only there so that a person looking in the directory can tell what they
    are looking at.
    """
    root = cache_dir()
    if root is None:
        return None
    try:
        absolute = str(Path(folder).resolve())
    except OSError:                 # pragma: no cover — an unreachable share
        swallowed(f"resolving {folder} for its scan cache")
        return None
    digest = hashlib.sha256(absolute.encode("utf-8", "replace")).hexdigest()[:16]
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(folder).name)[:40] or "plate"
    return root / f"{stem}-{digest}.json"


def _folder_of(paths: "list[Path]") -> "Path | None":
    """The one folder *paths* are in, or ``None`` when they span several."""
    parents = {Path(p).parent for p in paths}
    return parents.pop() if len(parents) == 1 else None


# ── the value encoding ──────────────────────────────────────────────────────
#
# Tagged rather than plain JSON, because the things being stored are not plain:
# ``tiles`` and ``scene_wells`` are keyed by *integer*, and JSON object keys are
# strings, so a round trip through untagged JSON turns scene 0 into scene "0"
# and every lookup misses. ``compressions`` and ``pixel_types`` are sets, and
# ``declared_scene_indices`` is a tuple. Each tag round-trips its type exactly.
#
# Anything else **raises**. A serialiser that quietly drops what it does not
# recognise means a field added to CziLayout next year is simply absent from
# every cached entry, and absent reads as "the file does not have that" — a
# silent substitution, which is the failure this whole area exists to prevent.

_DICT, _SET, _TUPLE, _OBJ = "#dict", "#set", "#tuple", "#obj"


def _encode(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, tuple):
        return {_TUPLE: [_encode(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        return {_SET: [_encode(item) for item in sorted(value)]}
    if isinstance(value, dict):
        return {_DICT: [[_encode(k), _encode(v)] for k, v in value.items()]}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = type(value).__name__
        if name not in _DECODABLE:
            raise TypeError(f"{name} is not in the scan cache's roster")
        return {_OBJ: name,
                "f": {f.name: _encode(getattr(value, f.name))
                      for f in dataclasses.fields(value)}}
    raise TypeError(f"the scan cache cannot store a {type(value).__name__}")


def _decode(value):
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    if _TUPLE in value:
        return tuple(_decode(item) for item in value[_TUPLE])
    if _SET in value:
        return {_decode(item) for item in value[_SET]}
    if _DICT in value:
        return {_decode(k): _decode(v) for k, v in value[_DICT]}
    if _OBJ in value:
        built = _DECODABLE.get(value[_OBJ])
        if built is None:
            raise ValueError(f"unknown cached type {value[_OBJ]!r}")
        known = {f.name for f in dataclasses.fields(built)}
        fields = value.get("f") or {}
        # Only the fields this build knows: an entry written by a version that
        # carried a field since removed must not become a TypeError, and one
        # missing a field added since takes that field's default.
        return built(**{k: _decode(v) for k, v in fields.items() if k in known})
    raise ValueError(f"unknown cached value {sorted(value)!r}")


# ── reading and writing ─────────────────────────────────────────────────────

def _stamp(path: Path) -> "tuple[int, int] | None":
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return int(info.st_size), int(info.st_mtime_ns)


def _read_file(where: Path) -> dict:
    """*where*'s entries, or ``{}`` for anything that cannot be trusted.

    A cache is a thing that may be deleted, truncated, written by a different
    build, or edited by hand, and none of those is an error a user should see:
    the answer is simply read from the files instead.
    """
    try:
        body = json.loads(where.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(body, dict):
        return {}
    if (body.get("format") != FORMAT_VERSION
            or body.get("parser") != PARSER_VERSION):
        return {}
    entries = body.get("files")
    return entries if isinstance(entries, dict) else {}


def load(paths: "list[Path]") -> int:
    """Put what the disk remembers about *paths* back into memory.

    Returns how many files it could answer for. Each entry is used only when
    the file's size and modification time still match what was recorded, so a
    plate re-exported since the last scan is read again and a plate that has
    not moved is not read at all.
    """
    folder = _folder_of(paths)
    if folder is None:
        return 0
    where = cache_file_for(folder)
    if where is None:
        return 0
    with _LOCK:
        entries = _read_file(where)
    if not entries:
        return 0
    restored = 0
    for path in paths:
        entry = entries.get(Path(path).name)
        stamp = _stamp(path)
        if not isinstance(entry, dict) or stamp is None:
            continue
        if (entry.get("size"), entry.get("mtime_ns")) != stamp:
            continue
        try:
            meta = entry.get("meta")
            if meta is not None:
                czi_read.remember_plate_metadata(Path(path), _decode(meta))
            for flag, layout in (entry.get("layouts") or {}).items():
                czi_read.remember_layout(Path(path), flag == "1",
                                         _decode(layout))
        except (TypeError, ValueError, KeyError):
            swallowed(f"a cached scan entry for {Path(path).name}")
            continue
        restored += 1
    return restored


def save(paths: "list[Path]") -> int:
    """Write what memory now holds about *paths*, merged with what is there.

    **Merged**, because a scan of one file of a folder knows nothing about its
    six hundred siblings, and writing only what this scan learned would throw
    the rest away — which would make the cache useless exactly on the workflow
    it exists for, a user opening one file of a plate and then the plate.

    Returns how many files the file ends up describing.
    """
    folder = _folder_of(paths)
    if folder is None:
        return 0
    where = cache_file_for(folder)
    if where is None:
        return 0
    with _LOCK:
        entries = dict(_read_file(where))
        for path in paths:
            name = Path(path).name
            entry = _entry_for(Path(path), entries.get(name))
            if entry is not None:
                entries[name] = entry
        # An entry whose file has gone is never going to be used again, and
        # dropping it here is the only sweep this cache gets.
        entries = {name: entry for name, entry in entries.items()
                   if (folder / name).exists()}
        if not entries:
            with swallowed_missing():
                where.unlink()
            return 0
        _write(where, entries)
        _prune(where.parent)
    return len(entries)


def _entry_for(path: Path, previous: "dict | None") -> "dict | None":
    """What to record for *path*: what memory holds, over what is already there.

    **Merged with the previous entry rather than written over it**, because the
    two passes a plate is read in learn different halves — a peek parses the
    plate document and never touches the subblock directory, a full read walks
    the directory and is handed the document — so whichever ran last may hold
    only its own half.

    Today that merge is defence rather than a fix: both entry points call
    :func:`load` before they scan, which puts the file's whole entry back into
    memory, so by the time :func:`save` reads memory it holds at least what the
    file did. It is here because that invariant is three call sites wide and
    nothing states it — an entry point that saved without having loaded, or a
    plate large enough to evict entries from ``czi_read``'s LRU between the two,
    would silently trade a cached layout for a cached document and the next
    full read would pay for the directory again with nothing saying why.
    Writing a cache entry should not be able to *remove* an answer.

    The previous entry is kept only while its stamp still matches the file, so
    a merge can never carry an answer about bytes that have since changed.
    """
    stamp = _stamp(path)
    if stamp is None:
        return None
    kept = previous or {}
    if (kept.get("size"), kept.get("mtime_ns")) != stamp:
        kept = {}
    meta = czi_read.cached_plate_metadata(path)
    layouts = dict(kept.get("layouts") or {})
    try:
        for read_times in (False, True):
            found = czi_read.cached_layout(path, read_times)
            if found is not None:
                layouts["1" if read_times else "0"] = _encode(found)
        encoded_meta = (kept.get("meta") if meta is None else _encode(meta))
        if encoded_meta is None and not layouts:
            return None
        return {"size": stamp[0], "mtime_ns": stamp[1],
                "meta": encoded_meta, "layouts": layouts}
    except TypeError:
        swallowed(f"encoding the scan of {path.name}")
        return None


def _write(where: Path, entries: dict) -> None:
    """Replace *where* atomically, or leave what is there alone.

    Through a temporary file in the same directory, so a process killed
    mid-write leaves the previous cache rather than a truncated one that the
    next run would have to detect.
    """
    body = {"format": FORMAT_VERSION, "parser": PARSER_VERSION,
            "files": entries}
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(where.parent),
            prefix=where.stem + ".", suffix=".tmp", delete=False)
        with handle:
            json.dump(body, handle)
        os.replace(handle.name, where)
    except OSError:
        swallowed(f"writing the scan cache {where.name}")
        if handle is not None:
            with swallowed_missing():
                os.unlink(handle.name)


def _prune(root: Path) -> None:
    try:
        found = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:                 # pragma: no cover — the directory went
        swallowed(f"listing the scan cache {root}")
        return
    for stale in found[:max(0, len(found) - _MAX_FOLDERS)]:
        with swallowed_missing():
            stale.unlink()


class swallowed_missing:
    """``contextlib.suppress(OSError)``, spelled so the rule can see it.

    ``core.safe`` and ``util.aw_safe`` exist so that a swallowed exception is
    always logged. Deleting a file that a parallel process has already deleted
    is the one shape where the log line is the noise rather than the signal —
    there is nothing to act on and nothing went wrong — so it is named instead
    of being written as a bare ``except OSError: pass``.
    """

    def __enter__(self) -> "swallowed_missing":
        return self

    def __exit__(self, kind, value, trace) -> bool:
        return kind is not None and issubclass(kind, OSError)


def forget(folder: Path) -> None:
    """Drop *folder*'s cache file. For tests, and for a user who wants a scan
    to actually happen."""
    where = cache_file_for(Path(folder))
    if where is not None:
        with swallowed_missing():
            where.unlink()
