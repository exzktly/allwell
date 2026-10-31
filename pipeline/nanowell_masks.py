"""The cell masks a nanowell run's segmenter returned, kept so a later run need not segment the same pixels again.

Segmentation is most of a nanowell run's time, and most of what a re-run segments it has segmented before: a
well re-run after an anchor was set by hand is located again from the same images under the same settings, and
the crops its segmenter is given are the same crops, pixel for pixel. Every reuse key a run already has
(``nanowell_task._prior_state``, the resume checkpoint) is keyed on the timepoint's *rows*, which an anchor
changes; this one is keyed on the segmenter's *input*, which it does not.

**What a mask is keyed on.** Exactly what decides the segmenter's output: the image it is given (its bytes, dtype
and shape), the range it is told to scale by (``lowhigh``), the area cap it is given (``max_size_fraction``),
and the segmenter's identity (``cache_identity``: for Cellpose its model, the cell diameter it is resized to,
the Cellpose version, the device kind, and a digest of the code that calls it — :func:`code_digest`). Anything
upstream that changes what the segmenter would see — the template the rim is divided by, the levels a
nanowell is shifted to, where a crop is cut, the registration — changes the bytes, so the key misses and the
crop is segmented again. Nothing is reused on the strength of an argument that the input "should be" the same.
What is stored is the segmenter's raw labels for its input, before ``segment_sites`` drops a neighbour's objects
or renumbers anything: everything after the segmenter is recomputed from them, as it would have been.

A segmenter without a ``cache_identity`` (a test's stand-in, or any segmenter handed in) is never cached: a
key without the segmenter in it could hand one segmenter's masks to another.

**Where it lives.** ``nanowells/<well>_masks.npz`` in the output folder, one file per well, so wells running in
separate processes never share a file. Each entry records the timepoint it was made for; when a run saves, it
keeps the entries it used, and the entries of timepoints it did not segment at all (an incremental run's kept
timepoints, the end-of-run pass's untouched ones). An entry of a timepoint the run did segment, and did not
use, describes pixels the run no longer gives the segmenter and is dropped, so the file does not grow with every
change of setting. A file that cannot be read is named in the log and treated as empty: a cache miss costs time,
never a wrong mask. The masks are stored zlib-compressed, each in the smallest unsigned integer type that holds
its labels; they are mostly zeros.

The store is shared by a well's tile threads (``nanowell_run._finish_timepoint``), so its dictionary is guarded
by a lock. The segmenter itself is still called under the worker's segmenter lock (``nanowell_parallel.serialised``
wraps the cached segmenter, and a hit holds that lock only for a dictionary lookup).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import zlib
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

#: The file's own layout. A file of another version is read as empty (named in the log).
MASKS_VERSION = 1
#: ``nanowells/<well>{MASKS_SUFFIX}``.
MASKS_SUFFIX = "_masks.npz"


def masks_path(nanowell_dir: Path, well: str) -> Path:
    """Where *well*'s masks are kept in *nanowell_dir* (an output folder's ``nanowells/``)."""
    return Path(nanowell_dir) / f"{well}{MASKS_SUFFIX}"


def code_digest(*modules) -> str:
    """A digest of every function and class method defined in *modules*, by their compiled code.

    Part of a segmenter's identity, so a change to the code between Cellpose and the labels it returns — how an
    image is normalised, padded or batched — retires every mask made before it without anyone having to remember a
    version number. Compiled code (``marshal`` of each code object) works in a frozen build, where the source may not
    be there; it also changes with the Python version and with line numbers, which costs a cache miss, never a
    wrong mask."""
    import inspect
    import marshal

    h = hashlib.blake2b(digest_size=12)
    for module in modules:
        h.update(module.__name__.encode())
        for name, obj in sorted(vars(module).items()):
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            members = [(name, obj)]
            if inspect.isclass(obj):
                members = [(f"{name}.{k}", v) for k, v in sorted(vars(obj).items())]
            for label, member in members:
                code = getattr(getattr(member, "__func__", member), "__code__", None)
                if code is not None:
                    h.update(label.encode())
                    h.update(marshal.dumps(code))
    return h.hexdigest()


def mask_key(identity: str, image, lowhigh, max_size_fraction) -> str:
    """The key of the labels a segmenter of *identity* returns for *image*, told *lowhigh* and *max_size_fraction*."""
    image = np.ascontiguousarray(image)
    h = hashlib.blake2b(digest_size=16)
    h.update(identity.encode())
    h.update(json.dumps([None if lowhigh is None else [float(v) for v in lowhigh],
                         None if max_size_fraction is None else float(max_size_fraction),
                         image.dtype.str, list(image.shape)]).encode())
    h.update(image.tobytes())
    return h.hexdigest()


def _pack(labels: np.ndarray) -> tuple[bytes, str, list]:
    labels = np.asarray(labels)
    top = int(labels.max()) if labels.size else 0
    if labels.size and int(labels.min()) < 0:
        raise ValueError("A segmenter returned a negative label")
    dtype = np.uint8 if top < 2 ** 8 else np.uint16 if top < 2 ** 16 else np.uint32
    return zlib.compress(np.ascontiguousarray(labels, dtype=dtype).tobytes(), 6), np.dtype(dtype).str, list(labels.shape)


def _unpack(packed: tuple[bytes, str, list]) -> np.ndarray:
    blob, dtype, shape = packed
    return np.frombuffer(zlib.decompress(blob), dtype=np.dtype(dtype)).reshape(shape).astype(np.int64)


class MaskStore:
    """One well's masks: those an earlier run left (read from *source*, if any) and those this run makes.

    :meth:`wrap` gives a segmenter that answers from the store where it can; :meth:`save` writes the store to
    *target*. :attr:`hits` and :attr:`misses` count the segmenter calls answered and made, for the run log."""

    def __init__(self, source: Path | None, target: Path):
        self.target = Path(target)
        self._lock = threading.Lock()
        self._prior: dict[str, tuple[str, tuple]] = {}        # key -> (timepoint, packed)
        self._used: dict[str, tuple[str, tuple]] = {}
        self._touched: set[str] = set()
        self.hits = self.misses = 0
        if source is not None and Path(source).is_file():
            self._prior = self._read(Path(source))

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            with np.load(path, allow_pickle=False) as data:
                index = json.loads(str(data["index"]))
                blob = data["blob"].tobytes()
        except (OSError, ValueError, KeyError, zlib.error) as exc:
            log.warning("The cell masks kept in %s could not be read, so every crop is segmented again (%s: %s).",
                        path, type(exc).__name__, exc)
            return {}
        if not isinstance(index, dict) or index.get("version") != MASKS_VERSION:
            log.warning("The cell masks kept in %s are of another layout (version %r, this run reads %d), so every "
                        "crop is segmented again.", path, index.get("version") if isinstance(index, dict) else None,
                        MASKS_VERSION)
            return {}
        out = {}
        for entry in index.get("entries", ()):
            start, stop = entry["at"]
            out[entry["key"]] = (entry["timepoint"], (blob[start:stop], entry["dtype"], entry["shape"]))
        return out

    def _get(self, key: str, timepoint: str) -> np.ndarray | None:
        with self._lock:
            found = self._used.get(key) or self._prior.get(key)
            if found is None:
                self.misses += 1
                return None
            self.hits += 1
            self._used[key] = (timepoint, found[1])
        return _unpack(found[1])

    def _put(self, key: str, timepoint: str, labels) -> None:
        packed = _pack(labels)
        with self._lock:
            self._used[key] = (timepoint, packed)

    def wrap(self, segment, timepoint: str):
        """*segment* answered from this store at *timepoint*, with the attributes ``segment_sites`` and
        ``segment_tile`` read; *segment* itself when it declares no ``cache_identity``."""
        identity = getattr(segment, "cache_identity", None)
        if identity is None:
            return segment
        with self._lock:
            self._touched.add(timepoint)

        def ident() -> str:
            return identity() if callable(identity) else str(identity)

        def call(image, lowhigh=None, max_size_fraction=None):
            key = mask_key(ident(), image, lowhigh, max_size_fraction)
            found = self._get(key, timepoint)
            if found is not None:
                return found
            kwargs = {k: v for k, v in (("lowhigh", lowhigh), ("max_size_fraction", max_size_fraction))
                      if v is not None}
            labels = np.asarray(segment(image, **kwargs))
            self._put(key, timepoint, labels)
            return labels

        for name in ("accepts_range", "accepts_size_cap"):
            if hasattr(segment, name):
                setattr(call, name, getattr(segment, name))
        many = getattr(segment, "segment_many", None)
        if many is not None:
            def segment_many(images, lowhigh, max_size_fraction=None):
                keys = [mask_key(ident(), image, lowhigh, max_size_fraction) for image in images]
                out = [self._get(key, timepoint) for key in keys]
                todo = [k for k, o in enumerate(out) if o is None]
                if todo:
                    kwargs = {} if max_size_fraction is None else {"max_size_fraction": max_size_fraction}
                    found = many([images[k] for k in todo], lowhigh, **kwargs)
                    for k, labels in zip(todo, found):
                        out[k] = np.asarray(labels)
                        self._put(keys[k], timepoint, out[k])
                return out
            call.segment_many = segment_many
        return call

    def save(self) -> None:
        """Write the masks this run used, and an earlier run's of the timepoints this run did not segment, to
        :attr:`target`, atomically. Nothing is written when there is nothing to keep and no file to replace."""
        with self._lock:
            keep = dict(self._used)
            for key, (tp, packed) in self._prior.items():
                if tp not in self._touched and key not in keep:
                    keep[key] = (tp, packed)
        if not keep and not self.target.exists():
            return
        entries, chunks, at = [], [], 0
        for key, (tp, (blob, dtype, shape)) in keep.items():
            entries.append({"key": key, "timepoint": tp, "dtype": dtype, "shape": list(shape),
                            "at": [at, at + len(blob)]})
            chunks.append(blob)
            at += len(blob)
        index = json.dumps({"version": MASKS_VERSION, "entries": entries})
        self.target.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.target.with_name(self.target.name + ".tmp")
        with open(tmp, "wb") as fh:
            np.savez(fh, index=np.array(index), blob=np.frombuffer(b"".join(chunks), dtype=np.uint8))
        os.replace(tmp, self.target)

    def summary(self) -> str:
        """One line for the run log: how many segmenter calls the store answered."""
        total = self.hits + self.misses
        if not total:
            return "no cell masks were needed"
        return (f"{self.hits} of {total} segmenter input(s) had their cell masks from an earlier run; "
                f"{self.misses} segmented")
