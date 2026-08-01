"""Which wells a run skips, and which it forces.

Stdlib-only (``pathlib`` plus the shared well-token normaliser), so it carries
no imaging-stack import — ``process_microscopy`` imports it, and the remote
runner ships it with the rest of the Qt-free pipeline.

Three decisions live here, and they are the same three every run makes per well:

* does this well already have usable output (``output_exists_for_well`` and its
  folder-mode twin), so a plain re-run can skip it;
* is this well on the caller's per-well force list
  (``_canonical_force_wells`` normalises that list, since ``C8`` and ``C08``
  name the same well and the two sides come from different places);
* and therefore, does *this* well run incrementally (``incremental_for_well``).
"""

from __future__ import annotations

from pathlib import Path

from util.well_token import (
    canonical_well_label as _canonical_well_label,
    safe_well as _safe_well,
)


def _canonical_force_wells(force_wells) -> frozenset[str]:
    """Normalise a per-well force list to canonical well labels.

    ``C8`` and ``C08`` name the same well, and the two sides of this comparison
    come from different places — the GUI field a user types (or Validate fills)
    on one side, a zip stem or folder name on the other. Comparing them raw
    would silently force nothing.
    """
    out = set()
    for raw in force_wells or ():
        label = _canonical_well_label(str(raw).strip())
        if label:
            out.add(label)
    return frozenset(out)


def incremental_for_well(incremental: bool, well_label: str, forced) -> bool:
    """``incremental``, except for a well the caller asked to force.

    An incremental run's done-set is the CSV's rows **unioned with the output
    archive's members** — deliberate, so a well whose CSV was lost but whose
    images survived is not needlessly re-segmented (the remote path relies on
    it). The cost is that a well with images and no CSV looks complete to an
    incremental run and is skipped, so the run can never regenerate the very
    CSV that is missing. Forcing that well means running it as if the flag were
    off: no done-set filter, a fresh CSV, a rewritten archive.
    """
    return incremental and _canonical_well_label(well_label) not in (forced or frozenset())


def output_exists_for_well(output_dir: Path, well_label: str, csv_prefix: str) -> bool:
    """
    Return True if processable output already exists for *well_label*.

    Both outputs must be present and non-empty to count as complete:
      • <output_dir>/<well>_out.zip       – compressed QC images
      • <output_dir>/<csv_prefix>_<well>.csv – per-well measurements

    This prevents re-processing wells that completed in a prior run. Note that a
    well whose CSV + out.zip exist counts as complete here even if some of its
    FOV-groups failed (the outputs cover only the successful FOV-groups), so a
    plain re-run SKIPS such partial wells. To reprocess the missing FOV-groups,
    re-run with --incremental (they are absent from the CSV done-set and so are
    re-processed) or --force (redo the whole well).
    """
    safe_well = _safe_well(well_label)
    out_zip  = output_dir / f"{well_label}_out.zip"
    csv_path = output_dir / f"{csv_prefix}_{safe_well}.csv"
    zip_ok = out_zip.exists()  and out_zip.stat().st_size  > 0
    csv_ok = csv_path.exists() and csv_path.stat().st_size > 0
    return zip_ok and csv_ok


def output_exists_for_well_folder(
    output_dir: Path,
    well_label: str,
    csv_prefix: str,
    compressed_output: bool = False,
) -> bool:
    """
    Return True if folder-mode output already exists for *well_label*.

    Both outputs must be present and non-empty to count as complete:
      • <output_dir>/<well>/                   – output image folder (must contain ≥1 file), OR
        <output_dir>/<well>_out.zip            – output image archive when *compressed_output* is True
      • <output_dir>/<csv_prefix>_<well>.csv   – per-well measurements
    """
    safe_well  = _safe_well(well_label)
    out_folder = output_dir / well_label
    out_zip    = output_dir / f"{well_label}_out.zip"
    csv_path   = output_dir / f"{csv_prefix}_{safe_well}.csv"
    folder_ok  = out_folder.is_dir() and any(out_folder.iterdir())
    zip_ok     = out_zip.exists() and out_zip.stat().st_size > 0
    csv_ok     = csv_path.exists() and csv_path.stat().st_size > 0
    out_ok     = zip_ok if compressed_output else folder_ok
    return out_ok and csv_ok
