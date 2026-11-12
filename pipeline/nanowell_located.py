"""Each nanowell timepoint's locating result, written by every run, so a later run named on it can take it as it is.

Locating a timepoint (``nanowell_run.locate_timepoint``) is the slow, image-heavy half of a nanowell run before any
cell is counted: registering the tiles, measuring the nanowells' pitch and diameter, detecting the sites, fitting the
array's outline, finding the central square, choosing the origin and giving every nanowell its address. A plate
analysed a second time with other segmentation or measurement settings needs none of that done again, so every run
writes what locating found, per timepoint, to ``nanowells/<well>_located.npz``, and a run given
``--nanowell_registration_from <folder>`` (the Analyze form's *Reuse registration*) takes each timepoint's result from
that folder's file instead of locating it (``nanowell_run.run_well``, *given_located*).

**What is taken is what was asked for, as it is.** Nothing about the images or the settings is compared: naming the
folder says these are this plate's positions and addresses. What is checked is only that the folder *fits* the plate
— a located result for every timepoint of every well the run analyses, over exactly the tiles the acquisition record
lists for it — and a folder that does not fit stops the run before any well starts, naming each well and timepoint
(:func:`refuse_unfitting_folder`), rather than locating some timepoints and taking others.

**What is kept** is the ``Located`` each timepoint was adopted with — the last locate of it in the run, so a timepoint
located again with the well's agreed tile layout, or by the end-of-run pass from the plate's motion, keeps that one —
captured when it is adopted, before the well's later steps (the shared roster, the diameter, a lattice-step frame
shift) adjust it, because a run that takes it applies those steps again exactly as the run that made it did. The
montage is not kept: it is rebuilt from the kept tile positions and the images, the cheap step; nor is the tile
plan, which is rebuilt from the record. A timepoint the run did not locate (an incremental run's kept ones) keeps
its earlier entry in the file. Entries are JSON with every type tagged and the arrays beside it, decoded only into
the classes ``_CLASSES`` names (no pickle).
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import os
from pathlib import Path
import zlib

import numpy as np

log = logging.getLogger(__name__)

#: The layout of an entry. A file of another layout cannot be taken (the run that names it is refused).
LOCATED_VERSION = 1
#: ``nanowells/<well>{LOCATED_SUFFIX}``.
LOCATED_SUFFIX = "_located.npz"

#: Classes an entry may hold, by ``module:qualname``. A file naming anything else is not decoded into it.
_CLASSES = {
    "pipeline.nanowell_lattice:LatticeFit",
    "pipeline.nanowell_lattice:IndexedPoint",
}

#: The ``Located`` fields that are not kept, and are rebuilt when an entry is taken (``nanowell_run._rebuilt``).
_REBUILT = ("montage", "plan")


def located_path(nanowell_dir: Path, well: str) -> Path:
    """Where *well*'s located timepoints are kept in *nanowell_dir* (an output folder's ``nanowells/``)."""
    return Path(nanowell_dir) / f"{well}{LOCATED_SUFFIX}"


# ── the entry codec ──────────────────────────────────────────────────────────
class NotKept(ValueError):
    """A ``Located`` holds something the codec does not keep; it is not written, and a run named on the folder is refused for it."""


def _encode(value, arrays: list, seen: dict):
    """*value* as JSON data, its arrays appended to *arrays*: every type is tagged so it decodes to the same type, and
    a dict or list met twice (a ``Located``'s ``fiducial`` is also its grid block's) decodes to one object again.
    *seen* maps ``id`` of each dict and list met so far to its number, in the order :func:`_decode` meets them."""
    if value is None or isinstance(value, (str, bool)) or type(value) in (int, float):
        return value
    if isinstance(value, np.generic):
        return {"np": value.dtype.str, "v": value.item()}
    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            raise NotKept("an object array")
        arrays.append(value)
        return {"nd": len(arrays) - 1}
    if isinstance(value, tuple):
        return {"tuple": [_encode(v, arrays, seen) for v in value]}
    if isinstance(value, (list, dict)):
        if id(value) in seen:
            return {"ref": seen[id(value)][0]}
        seen[id(value)] = (len(seen), value)       # the value is held, so its id is not reused while encoding
        if isinstance(value, list):
            return {"list": [_encode(v, arrays, seen) for v in value]}
        return {"dict": [[_encode(k, arrays, seen), _encode(v, arrays, seen)] for k, v in value.items()]}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = f"{type(value).__module__}:{type(value).__qualname__}"
        if name not in _CLASSES:
            raise NotKept(name)
        return {"dc": name, "f": [[f.name, _encode(getattr(value, f.name), arrays, seen)]
                                  for f in dataclasses.fields(value)]}
    raise NotKept(type(value).__qualname__)


def _decode(data, arrays, made: list):
    """:func:`_encode` undone; *made* collects each dict and list in the order they were numbered."""
    if data is None or isinstance(data, (str, bool, int, float)):
        return data
    if "np" in data:
        return np.dtype(data["np"]).type(data["v"])
    if "nd" in data:
        return arrays[data["nd"]]
    if "tuple" in data:
        return tuple(_decode(v, arrays, made) for v in data["tuple"])
    if "ref" in data:
        return made[data["ref"]]
    if "list" in data:
        out: list = []
        made.append(out)
        out.extend(_decode(v, arrays, made) for v in data["list"])
        return out
    if "dict" in data:
        out_d: dict = {}
        made.append(out_d)
        for k, v in data["dict"]:
            key = _decode(k, arrays, made)
            out_d[key] = _decode(v, arrays, made)
        return out_d
    if data.get("dc") in _CLASSES:
        module, qualname = data["dc"].split(":")
        cls = getattr(importlib.import_module(module), qualname)
        return cls(**{name: _decode(v, arrays, made) for name, v in data["f"]})
    raise ValueError(f"an entry names {data.get('dc')!r}, which is not a class an entry may hold")


def encode_located(located) -> tuple[bytes, list]:
    """``(compressed JSON, arrays)`` of a ``nanowell_run.Located``, without its montage and tile plan."""
    arrays: list = []
    seen: dict = {}
    fields = [[f.name, _encode(getattr(located, f.name), arrays, seen)] for f in dataclasses.fields(located)
              if f.name not in _REBUILT]
    return zlib.compress(json.dumps(fields).encode(), 6), arrays


def decode_located(blob: bytes, arrays: list) -> dict:
    """The ``Located`` fields :func:`encode_located` kept, by name."""
    made: list = []
    return {name: _decode(value, arrays, made) for name, value in json.loads(zlib.decompress(blob))}


# ── the file ─────────────────────────────────────────────────────────────────
def read_located(path: Path) -> dict[str, tuple[bytes, list]]:
    """``timepoint -> (entry, arrays)`` kept in *path*; raises ``OSError`` / ``ValueError`` when it cannot be read or
    is of another layout. An entry is decoded by :func:`decode_located`."""
    with np.load(path, allow_pickle=False) as data:
        index = json.loads(str(data["index"]))
        if not isinstance(index, dict) or index.get("version") != LOCATED_VERSION:
            raise ValueError(f"it is of another layout (version {index.get('version') if isinstance(index, dict) else None!r}"
                             f", this run reads {LOCATED_VERSION})")
        blob = data["blob"].tobytes()
        out = {}
        for n, entry in enumerate(index.get("entries", ())):
            start, stop = entry["at"]
            out[entry["timepoint"]] = (blob[start:stop], [data[f"a{n}_{k}"] for k in range(int(entry["arrays"]))])
        return out


def write_located(path: Path, entries: dict[str, tuple[bytes, list]]) -> None:
    """Write *entries* (``timepoint -> encode_located(...)``) to *path*, atomically, keeping the entries already there
    of every other timepoint. Nothing is written when there is nothing to keep."""
    path = Path(path)
    keep: dict = {}
    if path.is_file():
        try:
            keep = read_located(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("The located timepoints kept in %s could not be read, so only this run's are kept (%s: %s).",
                        path, type(exc).__name__, exc)
    keep.update(entries)
    if not keep:
        return
    index, chunks, members, at = [], [], {}, 0
    for n, (tp, (blob, arrays)) in enumerate(sorted(keep.items())):
        index.append({"timepoint": tp, "at": [at, at + len(blob)], "arrays": len(arrays)})
        chunks.append(blob)
        at += len(blob)
        members.update({f"a{n}_{k}": np.asarray(array) for k, array in enumerate(arrays)})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        np.savez_compressed(fh, index=np.array(json.dumps({"version": LOCATED_VERSION, "entries": index})),
                            blob=np.frombuffer(b"".join(chunks), dtype=np.uint8), **members)
    os.replace(tmp, path)


def given_located(folder, well: str) -> dict[str, dict]:
    """``timepoint -> Located fields`` of *well* in *folder*'s ``nanowells/`` (a ``_reused_from`` beside them names
    the folder). The run checked before it started that the file is there and fits (:func:`refuse_unfitting_folder`)."""
    path = located_path(Path(folder) / "nanowells", well)
    return {tp: {**decode_located(blob, arrays), "_reused_from": str(folder)}
            for tp, (blob, arrays) in read_located(path).items()}


def refuse_unfitting_folder(folder, well_zips, record_for, error) -> None:
    """Stop the run (*error* exits) unless *folder* holds a located result for every timepoint of every well in
    *well_zips* (``(label, zip path)``), over exactly the tiles the acquisition record lists for it. *record_for* maps a
    zip path to its acquisition record, or ``None`` when it has none (that well fails on its own, as it would without
    the folder). Every problem is named at once."""
    problems = []
    for well, zip_path in well_zips:
        record = record_for(zip_path)
        if record is None:
            continue
        wanted: dict[str, set] = {}
        for tile in record.get("tiles", ()):
            if tile.get("well") == well:
                wanted.setdefault(tile["timepoint"], set()).add(tile["field"])
        path = located_path(Path(folder) / "nanowells", well)
        if not path.is_file():
            problems.append(f"{well}: {path.name} is not there (the folder was analysed before located results were "
                            "kept, or without this well)")
            continue
        try:
            kept = read_located(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(f"{well}: {path.name} could not be read ({exc})")
            continue
        for tp in sorted(wanted):
            if tp not in kept:
                problems.append(f"{well} at {tp}: the folder has no located result for this timepoint")
                continue
            positions = decode_located(*kept[tp]).get("positions") or {}
            if positions and set(positions) != wanted[tp]:
                problems.append(f"{well} at {tp}: the folder's result covers tiles {sorted(positions)}, the "
                                f"acquisition record lists {sorted(wanted[tp])}")
    if problems:
        error(f"--nanowell_registration_from {folder} does not fit this plate, so nothing was run:\n  "
              + "\n  ".join(problems))
