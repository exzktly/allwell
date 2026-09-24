"""Normalising a *mixed* grouped input directory to one archive per well.

Qt-free and ``services``-free, because the pipeline runs it too
(``process_microscopy.main``) and the pipeline may not import the GUI's
services; ``services.input_resolution_service`` re-exports it under its old
name for the Analyze pane's resolver.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from util.dir_scan import files_and_subdirs, files_in
from util.image_exts import is_run_input_member
from util.retry_io import retry_io
from util.well_token import canonical_well_label
from util.zip_write import atomic_zip, replace_folder_with_archive

# Consolidation reads and writes on whatever volume the user pointed at as the
# input folder, and it moves the whole plate: every loose TIF into a per-well
# archive. On a network mount that load is enough to drop the session, and the
# unguarded write turned the blip into a failed run before analysis started
# (``OSError: [Errno 9] Bad file descriptor``). ``atomic_zip`` stages and
# commits with ``os.replace``, so re-running an attempt is safe.
CONSOLIDATION_ADVICE = (
    "A repeated failure here usually means the input volume is a network "
    "mount dropping its session under load. Copy the plate to local storage "
    "and point All-Well at that copy."
)


def consolidate_mixed_to_zips(
    folder: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> bool:
    """Normalise a *mixed* grouped dir (some ``<well>.zip`` + some ``<well>/``
    folders) to all-zips, so the pipeline reads every well.

    A hard-killed run can leave ``in/`` with zips for the wells it finished
    compressing and bare folders for the rest. ``process_microscopy`` prefers
    zips — ``well_folders`` is ignored whenever ANY zip is present — so the
    folder-only wells would be silently dropped. This zips each well folder that
    has no matching zip yet (atomic tmp→rename) and removes the now-redundant
    folder, mirroring the remote ``_stage_mixed_mode`` behaviour.

    Here, below both callers, because the pipeline's own resume advice needs
    it: a suspended or interrupted folder-mode run archives each well it
    finishes into ``in/<WELL>.zip``, so ``--incremental`` from the command line
    met exactly this mixed ``in/`` and ``process_microscopy`` refused it. The
    Analyze pane ran this first; the CLI did not. Folders for
    wells that already have a zip are left untouched (the zip wins), as are
    folders holding no TIFs at all — packing one would publish a member-less
    ``<WELL>.zip``. No-op unless the dir is genuinely mixed. Returns True iff it
    zipped at least one folder.
    """
    try:
        files, subdirs = files_and_subdirs(folder)
    except OSError:
        return False
    zip_wells = {
        canonical_well_label(p.stem)
        for p in files
        if p.suffix.lower() == ".zip" and canonical_well_label(p.stem)
    }
    if not zip_wells:
        return False  # pure folders / pure loose — folder mode handles it
    leftover: dict[str, Path] = {}
    for p in subdirs:
        tok = canonical_well_label(p.name)
        if tok and tok not in zip_wells:
            leftover[tok] = p
    if not leftover:
        return False

    if log_fn:
        log_fn(
            f"[zipper] Mixed input ({len(zip_wells)} zip(s) + "
            f"{len(leftover)} bare well folder(s)) — zipping the leftover "
            f"folder(s) so every well is processed.\n"
        )
    zipped_any = False
    for well, well_dir in sorted(leftover.items()):
        target = folder / f"{well}.zip"
        # Input images, by the one definition — this packs the *user's* well
        # folder into the archive the pipeline segments from, so a narrower
        # rule here silently drops a PNG plate's images out of the run.
        tifs = [p for p in files_in(well_dir) if is_run_input_member(p.name)]
        if not tifs:
            # Nothing to pack. Writing the archive anyway would publish a
            # member-less <WELL>.zip — which macOS refuses to expand and the
            # pipeline reads as a well whose images vanished — and deleting the
            # folder would destroy whatever non-TIF content it does hold.
            if log_fn:
                log_fn(
                    f"[zipper]   {well}: folder holds no TIFs — left as-is "
                    "(no zip written)\n"
                )
            continue
        def _pack(target=target, tifs=tifs) -> None:
            with atomic_zip(target) as zf:
                for t in tifs:
                    zf.write(t, arcname=t.name)

        retry_io(
            _pack, what=target.name, advice=CONSOLIDATION_ADVICE, log_fn=log_fn,
        )
        # Drop the TIFs the archive now holds, and the folder only when the
        # archive covers everything in it. The zero-TIF branch above already
        # refuses to destroy non-image content; this is the same rule applied
        # to the branch where the folder *does* yield images, which is where
        # the content was actually being lost.
        outcome = replace_folder_with_archive(well_dir, tifs)
        zipped_any = True
        if log_fn:
            tail = (
                ""
                if outcome.folder_removed
                else (f" — folder kept, {len(outcome.kept)} file(s) not "
                      f"archived: {', '.join(outcome.kept)}")
            )
            log_fn(
                f"[zipper]   {well}: folder → {well}.zip "
                f"({len(tifs)} file(s)){tail}\n"
            )
    return zipped_any
