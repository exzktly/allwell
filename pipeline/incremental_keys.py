"""Stdlib-only ``(fov, timepoint)`` done-key helpers for incremental runs.

Extracted from ``pipeline.io`` (audit A2). These functions read the *names* of
already-processed FOV-groups out of a per-well CSV, a ``<well>_out.zip``, or an
uncompressed output folder — they touch only ``csv`` / ``zipfile`` / ``pathlib``
and never the imaging stack. ``pipeline.io`` itself imports numpy / tifffile /
skimage at module scope, so anything that only needed these key readers used to
drag the whole imaging stack in. The remote-execution client (the GUI-side
incremental *split* that decides which FOVs to upload) must stay imaging-stack-
free, so ``services.remote_runner`` had kept hand-copied twins of these. They now
live here and both sides import them — one implementation, no heavy-import cost.

``pipeline.io`` re-exports these names for backward compatibility.
"""

from __future__ import annotations

import zipfile
from pathlib import Path


def read_done_fov_keys(csv_path: "Path | str") -> "set[tuple[str, str]]":
    """Return the ``(fov, timepoint)`` pairs already present in a per-well CSV.

    These are the FOV-groups an incremental run must NOT reprocess. The values
    are read straight from the CSV's ``fov`` / ``timepoint`` columns — the exact
    strings ``parse_filename`` produced when the rows were first written — so a
    freshly-parsed group's ``(fov, timepoint)`` compares equal without any
    re-normalisation. A missing / unreadable / column-less CSV yields an empty
    set (treated as "nothing done yet", i.e. process everything).
    """
    import csv

    path = Path(csv_path)
    if not path.exists():
        return set()
    keys: "set[tuple[str, str]]" = set()
    try:
        with open(path, newline="") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames or "fov" not in reader.fieldnames \
                    or "timepoint" not in reader.fieldnames:
                return set()
            for row in reader:
                keys.add((str(row.get("fov", "")), str(row.get("timepoint", ""))))
    except OSError:
        return set()
    return keys


def fov_tp_from_name(name: str, *, schema: str, sep: str) -> "tuple[str, str]":
    """Extract ``(fov, timepoint)`` tokens from a file/member basename using the
    schema's field indices — the same positional discipline ``parse_filename``
    applies, so the key compares equal to what a freshly-parsed input group and
    the per-well CSV produce. A processed-output basename keeps the full input
    stem plus a role suffix (``..._overlay``/``..._labels``); the suffix lands in
    a slot past ``timepoint`` and is ignored.
    """
    fields = [f.strip().lower() for f in str(schema or "").split(":")]
    fi = fields.index("fov") if "fov" in fields else -1
    ti = fields.index("timepoint") if "timepoint" in fields else -1
    parts = Path(name).stem.split(sep)
    fov = parts[fi] if 0 <= fi < len(parts) else ""
    tp = parts[ti] if 0 <= ti < len(parts) else ""
    return (fov, tp)


def read_done_fov_keys_from_zip(
    out_zip: "Path | str", *, schema: str, sep: str,
) -> "set[tuple[str, str]]":
    """``(fov, timepoint)`` pairs recoverable from a ``<well>_out.zip``'s members.

    The per-well CSV is the primary done-set source, but a processed well's
    measurements CSV can be absent while its output archive is present (e.g. a
    remote run that materialised only the zip, or a CSV that was moved/lost). The
    archive's image members keep the input stem, so their ``(fov, timepoint)``
    tokens recover exactly which FOV-groups were already processed — letting an
    incremental run skip them instead of re-segmenting already-done work. Missing
    / unreadable archives yield an empty set.
    """
    path = Path(out_zip)
    if not path.exists():
        return set()
    keys: "set[tuple[str, str]]" = set()
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return set()
    for name in names:
        if name.endswith("/"):
            continue
        keys.add(fov_tp_from_name(name, schema=schema, sep=sep))
    keys.discard(("", ""))
    return keys


def read_done_fov_keys_from_folder(
    folder: "Path | str", *, schema: str, sep: str,
) -> "set[tuple[str, str]]":
    """``(fov, timepoint)`` pairs recoverable from an uncompressed output well
    folder's files — the folder-mode twin of :func:`read_done_fov_keys_from_zip`
    (used when ``compress_output_well_folders`` is off). Missing folder ⇒ empty.
    """
    path = Path(folder)
    if not path.is_dir():
        return set()
    keys: "set[tuple[str, str]]" = set()
    try:
        for p in path.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            keys.add(fov_tp_from_name(p.name, schema=schema, sep=sep))
    except OSError:
        return set()
    keys.discard(("", ""))
    return keys


def incremental_done_fov_keys(
    prior_dir: "Path | str", well_label: str, *,
    csv_prefix: str, safe_well: str, schema: str, sep: str,
) -> "tuple[set[tuple[str, str]], bool]":
    """Done ``(fov, timepoint)`` keys for an incremental diff, from every prior
    artefact: the per-well CSV (primary) unioned with the processed-output
    archive (``<well>_out.zip``) and the uncompressed output folder. A
    missing/lost CSV then can't make an incremental run re-segment FOVs that
    already have outputs. Returns ``(keys, from_csv)`` — *from_csv* is False when
    the keys were recovered only from the outputs, so the caller can log it.
    """
    prior = Path(prior_dir)
    csv_keys = read_done_fov_keys(prior / f"{csv_prefix}_{safe_well}.csv")
    keys = set(csv_keys)
    keys |= read_done_fov_keys_from_zip(
        prior / f"{well_label}_out.zip", schema=schema, sep=sep)
    keys |= read_done_fov_keys_from_folder(prior / well_label, schema=schema, sep=sep)
    return keys, bool(csv_keys)
