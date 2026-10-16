"""What a batched run may reuse of an earlier attempt's staged uploads.

Batched remote mode (``services.remote_process._BatchedRemoteProcess``) uploads
batch *i* into ``<run_dir>/b<i>`` and the empty-well reference into
``<run_dir>/bg``, and the driver swaps each batch in with
``rm -rf in && mv b<i> in``. The driver is launched *before* batch 1 is
uploaded, so the allocation's queue wait overlaps the transfer — which means a
node that then fails to boot (``srun: error: Nodes … are still not ready`` /
``Something is wrong with the boot of the nodes``) ends the run with batch 1
uploaded and never touched.

A retry used to delete every staged directory first and send those wells again.
They are kept now, because the reuse is keyed completely: ``run_id`` — and so
the run directory — is derived from the name, size and whole-second mtime of
every staged archive (``services.remote_scratch.fingerprint_archives``) plus the
pipeline arguments. A file of a given name in that directory is therefore a
whole or partial copy of the archive this run would send under that name; a
changed archive changes the fingerprint and lands the run in a different
directory. rsync's quick check skips a whole copy and ``--partial`` resumes a
partial one.

What a kept directory can still hold is a well that belongs to a *different*
batch now — the local-completion filter in ``_run_batches`` can shift the
slicing between attempts — and the driver's ``mv`` hands the pipeline the whole
directory, so such a well would be processed twice. :func:`prune_staged` removes
everything not in the batch immediately before its upload.

The half-processed ``in/`` is not kept: nothing records which of its wells
finished, and ``_start_driver`` clears it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from services.remote_run_id import _parse_argv_flags
from util.well_token import background_well_key as _bg_well_key, blank_wells_for_run


def prune_argv(remote_dir: str, names: list[str]) -> list[str]:
    """``find`` that removes every entry of *remote_dir* not named in *names*
    and prints each one it keeps."""
    argv = ["find", remote_dir, "-mindepth", "1", "-maxdepth", "1", "("]
    for name in sorted(set(names)):
        argv += ["!", "-name", name]
    return argv + [")", "-exec", "rm", "-rf", "{}", "+", "-o", "-print"]


def prune_staged(cache: Any, remote_dir: str, zips: list[Path], label: str,
                 log: Callable[[str], None]) -> None:
    """Leave *remote_dir* holding nothing but (copies of) *zips*, and say how
    many of them an earlier attempt had already put there.

    *cache* is the run's ``DatasetCache``; only its ``_ssh_run`` is used.
    ``find`` exits non-zero when the directory does not exist yet — a first
    attempt's normal case — and the ``rm -rf`` fallback is then a no-op. On any
    other failure the fallback is the safe answer: clearing the directory costs
    a re-send, while a stray well left in a batch costs a duplicate result.
    """
    names = [z.name for z in zips]
    result = cache._ssh_run(prune_argv(remote_dir, names), check=False)
    if result is None or getattr(result, "returncode", 1) != 0:
        cache._ssh_run(["rm", "-rf", remote_dir], check=False)
        return
    kept = [line for line in (result.stdout or "").splitlines() if line.strip()]
    if kept:
        log(f"[remote] {label}: {len(kept)} of {len(set(names))} well-zip(s) "
            f"are already on the remote from an earlier attempt — rsync skips "
            f"any that arrived whole and resumes the rest\n")


def _empty_well_blanks(argv, well_zips) -> tuple[str, ...]:
    """The blank well archives this run needs for empty-well correction.

    Returns their stems, or empty when the run asks for no empty-well
    correction. Batched mode has to know about them because it slices the plate
    into batches and stages one at a time: the pipeline builds its correction
    from whatever wells are in the input directory *at that moment*, so a blank
    sitting in the last batch corrects the last batch and nothing else. Every
    earlier batch produced numbers with the background left in, and the output
    carried no sign of it.
    """
    flags = _parse_argv_flags(argv, multi=set(), single={
        "--background", "--flatfield", "--background_well", "--background_well_map"})
    if "empty_well" not in (flags.get("--background"), flags.get("--flatfield")):
        return ()
    # The pipeline's own parser and match key, not a fourth spelling of them.
    # A local `re.split(r"[,\s]+", ...)` here read "H12 G12" as two wells while
    # `split_background_wells` reads it as one, so batched mode held out and
    # uploaded two archives the pipeline then matched neither of — the run died
    # on the guard in every batch. `blank_wells_for_run` is that reading widened
    # to the map: a blank named only there is still a blank.
    wanted = {_bg_well_key(w) for w in blank_wells_for_run(
        flags.get("--background_well", ""), flags.get("--background_well_map", ""))}
    if not wanted:
        return ()
    return tuple(z.stem for z in well_zips if _bg_well_key(z.stem) in wanted)
