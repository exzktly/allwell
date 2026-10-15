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


def read_done_fov_keys(csv_path: Path | str) -> set[tuple[str, str]]:
    """Return the ``(fov, timepoint)`` pairs already present in a per-well CSV.

    These are the FOV-groups an incremental run must NOT reprocess. The values
    are read straight from the CSV's ``fov`` / ``timepoint`` columns — the exact
    strings ``parse_filename`` produced when the rows were first written — so a
    freshly-parsed group's ``(fov, timepoint)`` compares equal without any
    re-normalisation. A missing / unreadable / column-less CSV yields an empty
    set (treated as "nothing done yet", i.e. process everything).
    """
    import csv

    from util.csv_gz import GZ_READ_ERRORS, existing_csv, open_csv_text

    path = existing_csv(csv_path)
    if path is None:
        return set()
    keys: set[tuple[str, str]] = set()
    try:
        with open_csv_text(path) as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames or "fov" not in reader.fieldnames \
                    or "timepoint" not in reader.fieldnames:
                return set()
            for row in reader:
                keys.add((str(row.get("fov", "")), str(row.get("timepoint", ""))))
    except (OSError, *GZ_READ_ERRORS):     # a truncated or corrupt .csv.gz
        return set()
    return keys


def fov_tp_from_name(name: str, *, schema: str | list[str], sep: str) -> tuple[str, str]:
    """Extract ``(fov, timepoint)`` tokens from a file/member basename using the
    schema's field indices — the same positional discipline ``parse_filename``
    applies, so the key compares equal to what a freshly-parsed input group and
    the per-well CSV produce. A processed-output basename keeps the full input
    stem plus a role suffix (``..._overlay``/``..._labels``); the suffix lands in
    a slot past ``timepoint`` and is ignored.

    *schema* may be the colon-delimited string (the CLI / remote callers) or an
    already-parsed list of field names (the pipeline threads ``parse_schema``'s
    list). Both normalise to the same field tokens: passing the list straight to
    ``str(schema).split(":")`` used to yield the list's repr — no colons — so
    ``fov`` / ``timepoint`` were never found and every key collapsed to
    ``('', '')``, silently defeating out-zip/folder recovery when the CSV was
    absent (an incremental run then re-segmented already-done FOVs).
    """
    raw = schema if isinstance(schema, (list, tuple)) else str(schema or "").split(":")
    fields = [str(f).strip().lower() for f in raw]
    fi = fields.index("fov") if "fov" in fields else -1
    ti = fields.index("timepoint") if "timepoint" in fields else -1
    parts = Path(name).stem.split(sep)
    fov = parts[fi] if 0 <= fi < len(parts) else ""
    tp = parts[ti] if 0 <= ti < len(parts) else ""
    return (fov, tp)


def fov_keys_from_member_names(
    names, *, schema: str | list[str], sep: str, skip_hidden: bool = False,
    images_only: bool = False,
) -> set[tuple[str, str]]:
    """``(fov, timepoint)`` pairs for member/file names already in hand.

    The name-parsing half of :func:`read_done_fov_keys_from_zip` and
    :func:`read_done_fov_keys_from_folder`, split out so a caller that has
    already listed a container's members does not have to open it a second time
    just to get its keys. Both readers below delegate here, so the three call
    sites cannot drift.

    *skip_hidden* reproduces the folder reader's dotfile filter (``.DS_Store``
    and friends, which a real input folder carries and which parse to nothing);
    the zip reader passes ``False`` because it never had that filter, and
    quietly changing what an incremental run considers done is not this
    helper's job.

    *images_only* is the **intent**-side rule, and it is a different question
    from *skip_hidden*. A caller asking "how many FOV-groups does this plate
    hold?" is asking about the user's data; a plate carries a great deal that
    is not — an AppleDouble ``._Plate_A01_F1.tif`` beside every image, a
    ``__MACOSX/`` directory inside every zip written on a Mac, a ``.DS_Store``,
    and zip directory entries with no bytes at all. Those parse to *different*
    ``(fov, timepoint)`` keys rather than to nothing, so they do not fall out
    of the set: measured on a two-image well with its two macOS twins, intent
    came back 4 and two keys were permanently missing, so a finished plate
    reported ``partial`` for ever.

    It defaults to ``False`` deliberately. The **done**-side readers must keep
    the behaviour ``test_incremental_keys_shared.py::test_zip_reader_keeps_its_own_hidden_file_behaviour``
    pins, because changing what a resumed run considers already finished is a
    different decision from fixing what it counts as present, and making both
    at once would make neither reviewable.
    """
    from util.image_exts import is_run_input_member

    keys: set[tuple[str, str]] = set()
    for name in names:
        base = Path(name).name
        if not base or (skip_hidden and base.startswith(".")):
            continue
        if images_only and not is_run_input_member(name):
            continue
        keys.add(fov_tp_from_name(base, schema=schema, sep=sep))
    keys.discard(("", ""))
    return keys


def read_done_fov_keys_from_zip(
    out_zip: Path | str, *, schema: str | list[str], sep: str,
) -> set[tuple[str, str]]:
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
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return set()
    return fov_keys_from_member_names(
        [n for n in names if not n.endswith("/")], schema=schema, sep=sep)


def read_done_fov_keys_from_folder(
    folder: Path | str, *, schema: str | list[str], sep: str,
) -> set[tuple[str, str]]:
    """``(fov, timepoint)`` pairs recoverable from an uncompressed output well
    folder's files — the folder-mode twin of :func:`read_done_fov_keys_from_zip`
    (used when ``compress_output_well_folders`` is off). Missing folder ⇒ empty.
    """
    path = Path(folder)
    if not path.is_dir():
        return set()
    try:
        names = [p.name for p in path.iterdir() if p.is_file()]
    except OSError:
        return set()
    return fov_keys_from_member_names(
        names, schema=schema, sep=sep, skip_hidden=True)


def incremental_done_fov_keys(
    prior_dir: Path | str, well_label: str, *,
    csv_prefix: str, safe_well: str, schema: str | list[str], sep: str,
) -> tuple[set[tuple[str, str]], bool]:
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
