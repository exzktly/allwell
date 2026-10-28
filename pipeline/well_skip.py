"""Which wells a run skips, and which it forces.

Stdlib-only (``pathlib`` plus the shared well-token normaliser), so it carries
no imaging-stack import — ``process_microscopy`` imports it, and the remote
runner ships it with the rest of the Qt-free pipeline.

Three decisions live here, and they are the same three every run makes per well:

* is this well already finished here (``finished_output``: its output exists —
  ``output_exists_for_well`` and its folder-mode twin — under these settings,
  and is sealed whole, ``pipeline.run_report.finished_on_this_host``), so a
  plain re-run can skip it;
* is this well on the caller's per-well force list
  (``_canonical_force_wells`` normalises that list, since ``C8`` and ``C08``
  name the same well and the two sides come from different places), or
  outside the caller's ``--only_wells`` subset (``select_only_wells``);
* and therefore, does *this* well run incrementally (``incremental_for_well``).
"""

from __future__ import annotations

import logging
from pathlib import Path

from util.well_token import (
    canonical_well_label as _canonical_well_label,
    safe_well as _safe_well,
)

_log = logging.getLogger(__name__)


def out_zip_path(output_dir: Path, well_label: str) -> Path:
    """Where a well's compressed QC images live: ``<out>/<well>_out.zip``."""
    return Path(output_dir) / f"{well_label}_out.zip"


def out_folder_path(output_dir: Path, well_label: str) -> Path:
    """Where a well's uncompressed QC images live: ``<out>/<well>/``.

    Written instead of the archive when a run is told not to compress its
    output. A dataset is one or the other, never both — but a reader asking
    "is there image evidence for this well" has to check both, since it may be
    looking at a dataset some other run produced.
    """
    return Path(output_dir) / well_label


def csv_path_for_well(output_dir: Path, well_label: str, csv_prefix: str) -> Path:
    """Where a well's measurements live: ``<out>/<prefix>_<well>.csv``.

    The well token is passed through ``safe_well`` because this one becomes part
    of a filename the pipeline itself composes, and the three predicates below
    plus the end-of-run completeness check all have to compose it identically —
    a reader looking for ``measurements_C8.csv`` beside a writer that wrote
    ``measurements_C08.csv`` finds nothing and calls the well empty.
    """
    return Path(output_dir) / f"{csv_prefix}_{_safe_well(well_label)}.csv"


def existing_csv_for_well(output_dir: Path, well_label: str, csv_prefix: str) -> Path:
    """The file that holds a well's measurements: the ``.csv`` or the ``.csv.gz``.

    A finished well's CSV is gzipped, so "does it exist" has to look for both
    spellings. When neither exists the plain path is returned, so callers can
    keep asking ``.exists()`` of the result.
    """
    from util.csv_gz import existing_csv

    plain = csv_path_for_well(output_dir, well_label, csv_prefix)
    return existing_csv(plain) or plain


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


def select_only_wells(items, only_wells):
    """The ``(well_label, path)`` pairs a ``--only_wells`` run processes.

    *only_wells* ``None`` means the whole plate and returns *items* unchanged.
    Otherwise the pairs whose well is named — matched canonically, for the
    same reason as :func:`_canonical_force_wells` — are returned together with
    the named wells the input does not hold. The caller refuses the run on the
    second list: a well that was asked for and silently not run would read,
    afterwards, exactly like one that ran and was fine.
    """
    if only_wells is None:
        return list(items), []
    wanted = _canonical_force_wells(only_wells)
    kept = [(w, p) for w, p in items if _canonical_well_label(w) in wanted]
    present = {_canonical_well_label(w) for w, _p in items}
    return kept, sorted(wanted - present)


def scope_to_only_wells(well_zips, well_folders, only_wells, *, refuse, log, absent_ok: bool = False):
    """Narrow a run's ``(well_zips, well_folders)`` to its ``--only_wells``.

    Called once the plate's own well list has served everything that needs the
    whole plate (the blank pairing, the uncovered-well refusal); the flat-field
    and background models are then still built from the whole input, because a
    blank outside the subset is a reference, not a well whose output is wanted.
    *refuse* is the parser's ``error`` — a named well missing from the input
    stops the run before any image is read — unless *absent_ok*
    (``--only_wells_absent_ok``), for one batch of a batched remote run: every
    batch is told the whole subset and holds only its share, and the app has
    already refused a named well the plate lacks before uploading. Those wells
    are then logged as belonging to another batch.
    """
    if only_wells is None:
        return well_zips, well_folders
    zips, missing_z = select_only_wells(well_zips, only_wells)
    folders, missing_f = select_only_wells(well_folders, only_wells)
    missing = missing_z if well_zips else missing_f
    if missing and absent_ok:
        log.info("Only wells: %s not in this input (another batch's); this batch runs the named wells it holds.",
                 ", ".join(missing))
    elif missing:
        refuse(f"--only_wells names well(s) not in the input: {', '.join(missing)}")
    log.info("Only wells: %d of %d well(s) — %s. Every other well's output and "
             "records are left as they are.", len(zips or folders),
             len(well_zips or well_folders), ", ".join(w for w, _p in zips or folders))
    return zips, folders


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

    Existence only: a well whose CSV + out.zip exist passes here even if some of
    its FOV-groups failed or it was wound down part way (the outputs cover only
    the groups it finished). The run's skip asks :func:`finished_output`, which
    adds the settings and the completion seal, so such a well is run again.
    """
    out_zip  = out_zip_path(output_dir, well_label)
    csv_path = existing_csv_for_well(output_dir, well_label, csv_prefix)
    zip_ok = out_zip.exists()  and out_zip.stat().st_size  > 0
    csv_ok = csv_path.exists() and csv_path.stat().st_size > 0
    return zip_ok and csv_ok


def finished_output(output_dir: Path, well_label: str, csv_prefix: str, settings_key: str, *,
                    folder: bool = False, records: dict | None = None) -> bool:
    """The pipeline's own "skip this well": its output is here, made under these settings, and finished.

    Output and settings are :func:`output_exists_for_well` (or its folder-mode twin) and
    ``settings_record.well_settings_allow_reuse``; *finished* is ``run_report.finished_on_this_host`` — sealed, or
    output a build from before the seal finished. File existence alone answered "done" for a well that was wound
    down part way (a Stop's or a suspend's SIGTERM, the mid-batch watchdog), or that failed some of its groups: both
    write a CSV, an out.zip and a settings marker from the groups they finished. A remote resume re-sent such a well
    into the directory holding that output, the host skipped it, the partial copy was fetched as the well's result,
    and the run reported success (tracker B1). *records* is ``run_report.recorded_completeness`` of the output
    directory, read before the run rewrote its sidecar.
    """
    from pipeline.run_report import finished_on_this_host, seal_build, this_build
    from pipeline.settings_record import well_settings_allow_reuse

    exists = (output_exists_for_well_folder(output_dir, well_label, csv_prefix, compressed_output=True) if folder
              else output_exists_for_well(output_dir, well_label, csv_prefix))
    if not (exists and well_settings_allow_reuse(output_dir, well_label, settings_key)):
        return False
    if finished_on_this_host(output_dir, well_label, records=records):
        sealed_by, now = seal_build(output_dir, well_label), this_build()
        if sealed_by is not None and now and sealed_by != now:
            # Neither reuse key names the build (tracker F7), so a reuse across builds is said, per well.
            _log.info("Well %s was finished by %s, not this build (%s); its output is reused as it is — use "
                      "--force to recompute it with this build.", well_label,
                      f"build {sealed_by}" if sealed_by else "an earlier build", now)
        return True
    _log.info("Well %s has output here, but it was left part way (no completion seal) — running it again.",
              well_label)
    return False


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
    out_folder = out_folder_path(output_dir, well_label)
    out_zip    = out_zip_path(output_dir, well_label)
    csv_path   = existing_csv_for_well(output_dir, well_label, csv_prefix)
    folder_ok  = out_folder.is_dir() and any(out_folder.iterdir())
    zip_ok     = out_zip.exists() and out_zip.stat().st_size > 0
    csv_ok     = csv_path.exists() and csv_path.stat().st_size > 0
    out_ok     = zip_ok if compressed_output else folder_ok
    return out_ok and csv_ok
