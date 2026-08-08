"""Disk-capacity accounting for a run: what it will need, and what it has left.

A run touches two volumes, and neither of them is measured by looking at the
inputs:

* **Scratch** (``pipeline.io.scratch_base_for``) carries the transient working
  set — the extracted input TIFs plus roughly six intermediate images per
  FOV-group, written as float32 — for as many wells as ``--workers`` runs at
  once. It defaults to ``~/.allwell/scratch``, the *home* volume, which is
  usually not the volume holding the dataset.
* **The output volume** carries the durable results: a ``<well>_out.zip`` and a
  per-well CSV for every well, and those accumulate for the whole plate rather
  than being reclaimed between wells.

The inputs are compressed archives of 16-bit pixels; everything above is
uncompressed float32 while it is being written. "There are gigabytes free next
to my data" therefore says very little about whether the run can finish, and a
plate can exhaust a volume tens of times the size of its own input. This module
is the one place that turns that into numbers the run can act on:

* :func:`sample_image_bytes` reads one image's *uncompressed* size out of the
  dataset — from a loose TIF, or from a well archive's central directory, which
  costs one open and no extraction.
* :func:`preflight_scratch_space` sizes the transient working set and the
  durable plate output against the free space on the volume(s) that hold them,
  before a single image is read.
* :class:`OutputCapacityMonitor` replaces the up-front estimate with a
  measurement as soon as the first well lands: it projects the remaining plate
  from the output already written and warns while there is still time to act.
* :func:`enospc_message` turns a bare ``[Errno 28]`` into a sentence naming the
  volume, its free space, and the fact that the scratch churn may be sharing it.

Stdlib-only and Qt-free: the pipeline imports this on the remote worker, so it
must not pull in the imaging stack (``pipeline.io`` imports *this*, not the
other way round).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from util.disk_space import (
    directory_bytes,
    free_bytes,
    same_volume,
    space_message as enospc_message,
)

__all__ = [
    "OutputCapacityMonitor",
    "directory_bytes",
    "enospc_message",
    "estimate_group_scratch_bytes",
    "estimate_well_output_bytes",
    "free_bytes",
    "preflight_scratch_for_run",
    "preflight_scratch_space",
    "project_output_shortfall",
    "sample_image_bytes",
]

_GIB = float(1 << 30)


def _gib(n: float) -> str:
    """*n* bytes as a GiB string, for a log line a user has to act on."""
    return f"{n / _GIB:.1f} GiB"


# ── Sizing the dataset ──────────────────────────────────────────────────────

def sample_image_bytes(input_dir: Path, image_exts) -> int:
    """Uncompressed bytes of one input image, or 0 when none can be measured.

    Loose images are stat'ed. A zip-mode dataset — ``in/<WELL>.zip``, which is
    the layout the pipeline actually segments from — is read from the archive's
    central directory instead, so the number is the *uncompressed* member size
    without extracting anything. That distinction is the whole point: the
    archives are the only compressed copy in the run, and every byte the
    pipeline writes is uncompressed pixels derived from them.

    The largest member wins rather than the first: a well archive routinely
    carries a thumbnail or a single-channel snapshot alongside its full frames,
    and sizing the run from that would under-estimate the need this exists to
    catch.
    """
    input_dir = Path(input_dir)
    exts = {e.lower() for e in image_exts}
    for f in sorted(input_dir.rglob("*")):
        if f.is_file() and f.suffix.lower() in exts:
            try:
                return f.stat().st_size
            except OSError:
                return 0
    for zip_path in sorted(input_dir.glob("*.zip")):
        try:
            with zipfile.ZipFile(zip_path) as zf:
                sizes = [
                    info.file_size for info in zf.infolist()
                    if not info.is_dir()
                    and Path(info.filename).suffix.lower() in exts
                ]
        except (OSError, zipfile.BadZipFile):
            continue
        if sizes:
            return max(sizes)
    return 0


# Intermediates written per FOV-group (corrected + top-hat per channel, the
# label image, the overlay). Deliberately conservative — under-estimating the
# need is the failure this exists to prevent.
_INTERMEDIATES_PER_GROUP = 6
# float32 intermediates against 16-bit inputs.
_INTERMEDIATE_WIDENING = 2.0


def estimate_group_scratch_bytes(sample_image_bytes: int, n_channels: int) -> int:
    """Transient scratch one FOV-group needs: its inputs plus its intermediates."""
    sample = max(0, int(sample_image_bytes))
    inputs = sample * max(1, int(n_channels))
    intermediates = int(sample * _INTERMEDIATE_WIDENING * _INTERMEDIATES_PER_GROUP)
    return inputs + intermediates


def estimate_well_output_bytes(
    sample_image_bytes: int, groups_per_well: int
) -> int:
    """Durable output one well leaves on the output volume.

    ``<well>_out.zip`` holds the same intermediates the scratch staging did, so
    the estimate is the staging's intermediate half — with **no credit for
    deflate**. Compressed microscopy pixels are close to incompressible and the
    achieved ratio varies by orders of magnitude between a sparse mask and a
    dense fluorescence frame, so any assumed ratio would be a number invented
    here rather than measured. Over-estimating is why the output half of
    :func:`preflight_scratch_space` only ever warns, and why
    :class:`OutputCapacityMonitor` replaces this with a measurement as soon as
    the first well has actually been written.
    """
    sample = max(0, int(sample_image_bytes))
    per_group = int(sample * _INTERMEDIATE_WIDENING * _INTERMEDIATES_PER_GROUP)
    return per_group * max(1, int(groups_per_well))


# ── Up-front preflight ──────────────────────────────────────────────────────

def preflight_scratch_space(
    scratch_base: Path,
    *,
    sample_image_bytes: int,
    n_channels: int,
    groups_per_well: int,
    concurrent_wells: int,
    output_dir: Path | None = None,
    n_wells: int = 0,
) -> tuple[bool, str]:
    """Is there room for this run? ``(ok, message)``.

    ``ok`` is False only when the scratch volume cannot hold even a single
    well's staging — a run that starts there cannot finish, and saying so now
    beats thousands of dropped FOV-groups. Everything else returns True with a
    warning, because the estimates are deliberately rough and refusing to start
    on a heuristic would be worse than the risk they guard.

    When *output_dir* is given the durable plate output is checked too. The two
    needs are summed against one free figure when scratch and output share a
    volume — the default configuration, where ``~/.allwell/scratch`` and an
    output directory under the home volume compete for the same bytes, and
    checking either alone says the run fits when it does not.
    """
    free = free_bytes(scratch_base / "probe")
    if free is None:
        return True, ""
    per_group = estimate_group_scratch_bytes(sample_image_bytes, n_channels)
    per_well = per_group * max(1, int(groups_per_well))
    scratch_need = per_well * max(1, int(concurrent_wells))
    move_it = (
        "Scratch is not the dataset volume: set ALLWELL_SCRATCH_DIR (or the "
        "Analyze tab's Scratch disk field) to move it."
    )
    where = (
        f"{_gib(free)} free on {scratch_base} — the run needs about "
        f"{_gib(scratch_need)} there ({concurrent_wells} well(s) at once, "
        f"{groups_per_well} FOV-group(s) each). {move_it}"
    )
    if free < per_well:
        return False, f"Not enough scratch space to process even one well: {where}"

    output_need = 0
    shared = False
    if output_dir is not None and int(n_wells) > 0:
        output_need = estimate_well_output_bytes(
            sample_image_bytes, groups_per_well) * int(n_wells)
        shared = same_volume(Path(scratch_base), Path(output_dir))

    if shared and free < scratch_need + output_need:
        return True, (
            f"Disk space is tight and the run may stop part-way with 'No space "
            f"left on device': {_gib(free)} free on the volume holding BOTH the "
            f"scratch staging ({scratch_base}) and the output ({output_dir}). "
            f"The run needs about {_gib(scratch_need)} of transient staging plus "
            f"roughly {_gib(output_need)} of results for {n_wells} well(s) — the "
            f"results accumulate for the whole plate and are not reclaimed "
            f"between wells. {move_it}"
        )
    if free < scratch_need:
        return True, f"Scratch space is tight and FOV-groups may fail part-way: {where}"
    if output_need:
        out_free = free_bytes(Path(output_dir) / "probe")
        if out_free is not None and out_free < output_need:
            return True, (
                f"The output volume may fill before the plate finishes: "
                f"{_gib(out_free)} free on {output_dir}, and {n_wells} well(s) of "
                f"results are roughly {_gib(output_need)}. Results accumulate for "
                f"the whole plate — nothing is reclaimed between wells."
            )
    return True, ""


def preflight_scratch_for_run(
    scratch_base: Path,
    input_dir: Path,
    *,
    n_wells: int,
    n_channels: int,
    workers: int,
    image_exts,
    count_groups,
    log,
    output_dir: Path | None = None,
) -> None:
    """Warn — or refuse to start — when the run cannot fit on its volumes.

    Sized from this dataset rather than a constant: :func:`sample_image_bytes`
    gives the per-image byte size (from a loose TIF *or* a well archive's
    central directory), *count_groups* (the run's own field × timepoint
    enumeration) gives the FOV-groups per well, and *workers* gives how many
    wells stage at once. Exits only when there is not room for a single well,
    which cannot succeed; everything else warns.
    """
    log.info("Scratch  : %s", scratch_base)
    if n_wells <= 0:
        return
    sample = sample_image_bytes(input_dir, image_exts)
    if sample <= 0:
        return                       # nothing measurable in the input
    try:
        groups_per_well = int(count_groups())
    except Exception:
        groups_per_well = 1
    ok, message = preflight_scratch_space(
        scratch_base,
        sample_image_bytes=sample,
        n_channels=n_channels,
        groups_per_well=max(1, groups_per_well),
        concurrent_wells=max(1, min(workers, n_wells)),
        output_dir=output_dir,
        n_wells=n_wells,
    )
    if not ok:
        import sys as _sys
        log.error("%s Exiting before processing anything.", message)
        _sys.exit(2)
    if message:
        log.warning("%s", message)


# ── Mid-run projection ──────────────────────────────────────────────────────

def project_output_shortfall(
    *,
    written: int,
    wells_done: int,
    wells_total: int,
    free: int | None,
    output_dir: Path,
) -> str | None:
    """Warning text when the wells still to come will not fit, else ``None``.

    The per-well figure is *measured* — the output actually on disk divided by
    the wells that produced it — so unlike the up-front estimate it carries the
    real compression ratio, the real FOV count, and the real channel set.
    """
    if free is None or wells_done <= 0 or wells_total <= wells_done or written <= 0:
        return None
    per_well = written / wells_done
    remaining = wells_total - wells_done
    need = per_well * remaining
    if need <= free:
        return None
    return (
        f"The output volume will not hold the rest of this plate: {_gib(free)} "
        f"free on {output_dir}, and the {remaining} well(s) still to run need "
        f"about {_gib(need)} at the {_gib(per_well)} per well measured from the "
        f"{wells_done} already written. The run will start failing wells with "
        f"'No space left on device'. Free space now — the wells already written "
        f"are complete, and re-running with --incremental resumes the rest."
    )


class OutputCapacityMonitor:
    """Projects the plate's remaining output need from the wells already written.

    Fed one call per completed well by the worker pool. It walks the output
    directory (a few dozen entries, once per well) rather than trusting an
    estimate, and warns at most once — the point is to reach the user while
    there is still room to act, not to repeat itself for every remaining well.
    """

    def __init__(self, output_dir: Path, total_wells: int, *, log) -> None:
        self._output_dir = Path(output_dir)
        self._total = max(0, int(total_wells))
        self._log = log
        self._done = 0
        self._warned = False

    def well_done(self) -> None:
        """Record a finished well and warn if the remainder will not fit."""
        self._done += 1
        if self._warned or self._done >= self._total:
            return
        message = project_output_shortfall(
            written=directory_bytes(self._output_dir),
            wells_done=self._done,
            wells_total=self._total,
            free=free_bytes(self._output_dir / "probe"),
            output_dir=self._output_dir,
        )
        if message:
            self._warned = True
            self._log.warning("%s", message)
