"""Turning the user's input into the per-well archives a remote run uploads.

Whatever layout the plate is in — one zip per well, well folders, loose TIFs at
the top level, or a mix left by an interrupted zip-up — :func:`stage_local_zips`
returns a :class:`ZippedInput`: a directory of ``{well}.zip`` archives plus what
the caller must do afterwards. On the way it applies the channel filter and,
for a resumed run, the incremental split, both by planning from each archive's
member list before reading a byte of image data, and writing a staged copy only
of the archives that actually change.

**The user's archives are read-only here.** A filter or a split writes to a
staging directory beside the input, never over the originals; the
``ZippedInput.cleanup`` the caller runs removes it, and a failure part way
through removes it before the error propagates. Folder and loose-image plates
are zipped into the input directory itself, deliberately, so the next run
finds zips and skips the zip-up — and each such archive holds every image its
folder had, because it replaces that folder as the user's copy of the well.
The channel filter and the split then treat it exactly as they treat an
archive the user made: read, never rewritten.

What leaves the user's input is only ever a file proven to be inside an
archive beside it — a folder's images once its archive is published, a loose
image once the success hook has matched its name, size and CRC-32 against an
archive member. Anything else stays, and a well that is not uploaded because
there was nothing to pack is named in the log.
"""

from __future__ import annotations
import copy
import os
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from services.acquisition_transport import ZippedInput, carry_staged_record
from pathlib import Path
from collections.abc import Callable, Iterable
from services.remote_config import (
    REWRITE_MIN_SAVING,
)
from util.dir_scan import files_in, subdirs_in
from util.image_exts import GROUP_IMAGE_EXTS, is_run_input_member
from pipeline.well_skip import _canonical_force_wells
from util.well_token import canonical_well_label as _canonical_well_label
from util.well_token import (
    background_well_key as _bg_well_key,
    split_background_wells,
)
from pipeline.incremental_keys import (
    fov_tp_from_name,
    read_done_fov_keys,
    read_done_fov_keys_from_zip,
)
from util.zip_write import atomic_zip, replace_folder_with_archive
from util.well_token import is_valid_well_name
from util.retry_io import retry_io
from services.remote_run_state import (
    LocalInputMode,
    _canonical_well_from_path,
)
from services.remote_wire import (
    _TRANSFER_ADVICE,
)
from services.remote_transport import (
    _fmt_bytes,
    _fmt_secs,
)
from services.remote_scratch import (
    _new_scratch_dir,
    _new_transit_dir,
    _well_zips_in,
    sweep_stale_scratch,
)



def _without_finished(well_zips: tuple[Path, ...], wells: Iterable[str]) -> tuple[Path, ...]:
    """*well_zips* without the archives of *wells* (canonical labels)."""
    skip = set(wells)
    return tuple(p for p in well_zips if (_canonical_well_label(p.stem) or p.stem) not in skip)


def _within_only_wells(well_zips: tuple[Path, ...], only_wells, background_wells) -> tuple[Path, ...]:
    """*well_zips* narrowed to *only_wells* and the empty-well blanks; all of them when there is no subset.

    A named well the input lacks is not this function's to refuse: it is absent here and absent after, and
    ``scope_staged_to_only_wells`` refuses the run on it before anything is uploaded.
    """
    if not only_wells:
        return tuple(well_zips)
    wanted = set(_canonical_force_wells(only_wells))
    blanks = {_bg_well_key(w) for w in split_background_wells(background_wells or "")}
    return tuple(p for p in well_zips
                 if (_canonical_well_label(p.stem) or p.stem) in wanted or _bg_well_key(p.stem) in blanks)


@carry_staged_record
def stage_local_zips(
    input_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
    fluor_tokens: Iterable[str] | None = None,
    nuclear_token: str | None = None,
    log_fn: Callable[[str], None] | None = None,
    proc_hook: Callable[[subprocess.Popen | None], None] | None = None,
    move_source_tifs: bool = False,
    incremental: bool = False,
    prior_output_dir: Path | None = None,
    csv_prefix: str = "measurements",
    force_wells: Iterable[str] | None = None,
    background_wells: Iterable[str] | str | None = None,
    skip_wells: Iterable[str] = (),
    only_wells: Iterable[str] | None = None,
    on_well_count: Callable[[int], None] | None = None,
) -> ZippedInput:
    """Return a directory of per-well zips for ``input_dir``.

    For inputs that are already ``{well}.zip``, this is a no-op pointer.
    For loose-image or folder layouts, runs the existing
    ``well_plate_zipper`` (re-used via :mod:`services.input_resolution_service`)
    to group the images into well folders, and packs each folder into a
    full ``{well}.zip`` beside it in the input directory.

    Selective staging: when ``fluor_tokens`` is restricted, every layout's
    archives go through :func:`_plan_staging` / :func:`_materialize_staging`,
    which write a copy holding only the requested channels into a transit
    directory and upload that — cutting upload by the unused-channel fraction
    while the user's archive keeps every channel. The nuclear token is always
    kept (segmentation needs it).

    *skip_wells* are wells already finished locally
    (``services.remote_finished_wells``): in zip layout their archives are left
    out before anything is planned or rewritten. The caller removes them from
    the other layouts' result (``drop_finished``), which also lists them as up
    to date.

    *only_wells* (a ``--only_wells`` subset) keeps every other well's archive
    out of planning, so a channel filter or an incremental split reads and
    rewrites only the named wells and the empty-well blanks
    (*background_wells*). Without it a forced four-well run rewrote a transit
    copy of every archive on the plate before the subset was applied. It is an
    economy, not the subset itself: the caller still applies
    ``pipeline_runner.scope_staged_to_only_wells`` to whatever comes back,
    because the layouts that are not planned (an unfiltered ZIP plate, the
    MIXED resume) return every archive, and the whole-well zip-up of a FOLDER
    or FLAT plate still covers the plate, since its archives become the user's
    copy of each well.

    *on_well_count* is told, once, how many well archives the run can hold at
    most — as soon as every layout knows it and before anything is planned or
    rewritten — so a scheduler job can be queued while the rest of staging
    runs (``services.remote_reservation``). Planning only removes wells, so
    the count is an upper bound.
    """
    input_dir = input_dir.resolve()

    def _counted(well_zips: tuple[Path, ...]) -> tuple[Path, ...]:
        if on_well_count is not None:
            on_well_count(len(_within_only_wells(tuple(well_zips), only_wells, background_wells)))
        return well_zips
    # Reclaim local scratch leaked by a previously-killed run before we stage a
    # fresh (potentially multi-GB) copy of the input. The input volume's own
    # transit directory is swept too — that is where a rewritten archive is
    # staged now, so sweeping only the home scratch would have moved the leak
    # rather than removed it.
    sweep_stale_scratch(also=[input_dir], log_fn=log_fn)
    # Scanning the input volume to classify the layout is itself slow enough to
    # look like a hang on a spinning external disk, and it is the very first
    # thing that happens after "[remote] target:". Say so before starting.
    if log_fn is not None:
        log_fn(f"[remote] scanning input: {input_dir}\n")
    mode = _detect_local_input_mode(input_dir)
    if log_fn is not None:
        log_fn(f"[remote] input layout: {mode.value}\n")

    def _planned(well_zips, keep_tokens, zip_dir, *, prior_cleanup=None,
                 on_success=None) -> ZippedInput:
        """Plan, stage and describe *well_zips*: the channel filter and the
        incremental split, whichever apply. Every layout's archives come
        through here on an incremental run — the split is what the remote
        side's no-re-attach, output wipe and merging fetch all key on.

        Planned before anything moves: a member list is a small read of the
        central directory, so only the members to be uploaded are ever read —
        copying whole archives first dragged a NAS plate across the network."""
        planned = _within_only_wells(well_zips, only_wells, background_wells)
        outside = tuple(p.stem for p in well_zips if p not in planned)
        well_zips = planned
        if log_fn is not None:
            log_fn(f"[remote] planning {len(well_zips)} well archive(s)…\n")
        plans, up_to_date_list, zero_match = _plan_staging(
            well_zips,
            keep_tokens=keep_tokens,
            incremental=incremental,
            prior_output_dir=prior_output_dir,
            csv_prefix=csv_prefix,
            filename_schema=filename_schema,
            filename_sep=filename_sep,
            force_wells=_canonical_force_wells(force_wells),
            background_wells=frozenset(
                _bg_well_key(w) for w in split_background_wells(background_wells or "")),
            log_fn=log_fn,
        )
        if log_fn is not None:
            # Planning names only the wells it drops or rewrites; without this
            # a plate staging mostly unchanged went silent and read as a stall.
            log_fn(f"[remote] planned {len(plans)} well archive(s) to upload, "
                   f"{len(up_to_date_list)} already up to date.\n")
        _raise_on_zero_channel_match(zero_match)
        staged = _stage_plans(plans, zip_dir, mode=mode, incremental=incremental,
                              up_to_date=tuple(up_to_date_list), prior_cleanup=prior_cleanup,
                              on_success=on_success, log_fn=log_fn)
        staged.outside_subset_wells = outside
        return staged

    def _then_split(zin: ZippedInput) -> ZippedInput:
        # Grouping and zip-up produce whole-well archives; the split is applied
        # after them, so a resumed plate in any layout uploads only what is
        # missing rather than being recomputed — or, batched, skipped as done.
        if not incremental:
            return zin
        return _planned(zin.well_zips, None, zin.zip_dir, prior_cleanup=zin.cleanup,
                        on_success=zin.on_pipeline_success)

    if mode is LocalInputMode.ZIP:
        well_zips = tuple(sorted(_well_zips_in(input_dir)))
        if not well_zips:
            raise RuntimeError(
                f"Input dir reports zip-mode but contains no A01.zip-style files: {input_dir}"
            )
        well_zips = _counted(_without_finished(well_zips, skip_wells))
        if fluor_tokens is None and not incremental:
            # No filtering — use the user's zips directly (read-only).
            return ZippedInput(zip_dir=input_dir, mode=mode, well_zips=well_zips, cleanup=None)
        # The user's input dir is read-only: a filter or split writes to a
        # staging dir, never over their archives (a mistyped channel once
        # dropped every fluor image from the originals on disk).
        return _planned(well_zips, (_required_tokens(nuclear_token, fluor_tokens)
                                    if fluor_tokens is not None else None), input_dir)

    if mode is LocalInputMode.MIXED:
        # v5.2.21: input has zips for some wells and folders for
        # others — the signature of a previous run that died partway
        # through compress_input_well_folders. Resume by trusting the
        # existing zips and zipping the leftover folders into a
        # staging dir.
        mixed = _stage_mixed_mode(
            input_dir, fluor_tokens=fluor_tokens, nuclear_token=nuclear_token,
            log_fn=log_fn)
        _counted(mixed.well_zips)
        return _then_split(mixed)

    # Folder or flat mode. Loose images are grouped into ``<well>/`` folders by
    # well_plate_zipper (in ``input_dir`` itself), then each well folder is
    # packed into ``<well>.zip`` beside it, holding **every** image the folder
    # had. That archive is the user's copy of the well from then on — the next
    # run finds it and skips the zip-up — so it is never filtered: the channel
    # filter and the incremental split run afterwards through the same
    # plan-then-materialise pass as ZIP mode, which writes the trimmed copies
    # into a transit directory and leaves the archives in ``input_dir`` alone.
    #
    # Filtering these archives in place is what this used to do, on the theory
    # that they were scratch and the loose sources were still intact. They were
    # not: each folder's images are deleted as soon as its archive is
    # published, so rewriting the archive down to the selected channels
    # deleted every other channel from the only copy on disk, before a byte was
    # uploaded and whatever the run then did.
    well_zips, _on_success = _zip_up_in_place(
        input_dir, mode, filename_schema=filename_schema, filename_sep=filename_sep,
        log_fn=log_fn, proc_hook=proc_hook, move_source_tifs=move_source_tifs)
    _counted(well_zips)
    if fluor_tokens is None and not incremental:
        return ZippedInput(zip_dir=input_dir, mode=mode, well_zips=well_zips,
                           cleanup=None, on_pipeline_success=_on_success)
    # The same planner as ZIP mode: the user's archives are read, and only the
    # copies that have to change are written, into a transit directory.
    return _planned(well_zips, (_required_tokens(nuclear_token, fluor_tokens)
                                if fluor_tokens is not None else None),
                    input_dir, on_success=_on_success)


def _stage_plans(
    plans: list[_StagePlan],
    zip_dir: Path,
    *,
    mode: LocalInputMode,
    incremental: bool,
    up_to_date: tuple[str, ...],
    prior_cleanup: Callable[[], None] | None,
    on_success: Callable[[], None] | None,
    log_fn: Callable[[str], None] | None,
) -> ZippedInput:
    """Write the archives *plans* rewrite into a staging dir and describe the upload.

    The staging dir is opened only if something actually has to be rewritten;
    when the plans drop nothing from any archive there is no local copy at all.
    It holds copies only — the user's archives are read — so removing it, on
    cleanup or on a failure part way through, removes nothing of theirs.
    """
    staging = _open_staging_if_needed(plans, log_fn=log_fn)
    try:
        if log_fn is not None and staging is not None:
            rewrites = [p_ for p_ in plans if p_.keep is not None]
            log_fn(f"[remote] rewriting {len(rewrites)} well archive(s), "
                   f"{sum(len(p_.keep) for p_ in rewrites)} member(s) → {staging}\n")
        return ZippedInput(
            zip_dir=staging or zip_dir, mode=mode,
            well_zips=_materialize_staging(plans, staging, log_fn=log_fn),
            cleanup=_both(prior_cleanup, None if staging is None else
                          (lambda d=staging: shutil.rmtree(d, ignore_errors=True))),
            on_pipeline_success=on_success, incremental=incremental,
            up_to_date_wells=up_to_date)
    except Exception:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _zip_up_in_place(
    input_dir: Path,
    mode: LocalInputMode,
    *,
    filename_schema: str,
    filename_sep: str,
    log_fn: Callable[[str], None] | None,
    proc_hook: Callable[[subprocess.Popen | None], None] | None,
    move_source_tifs: bool,
) -> tuple[tuple[Path, ...], Callable[[], None]]:
    """Group a FOLDER / FLAT plate into full ``<well>.zip`` archives in *input_dir*.

    Returns the archives and the success hook that removes the loose top-level
    images they now hold. See the comment at the call in
    :func:`stage_local_zips` for why these archives are never filtered.
    """
    from services.input_resolution_service import run_zipper

    # Snapshot the loose images BEFORE running the zipper, so the post-success
    # deletion is judged against exactly what the user started with. The
    # snapshot is only a candidate list: the hook deletes a file only once its
    # bytes are proven to be inside an archive (see ``_on_success``).
    source_loose_images: tuple[Path, ...] = tuple(sorted(
        p for p in files_in(input_dir) if is_run_input_member(p.name)))
    if mode is LocalInputMode.FOLDER and source_loose_images and log_fn:
        # A prior ``--move-source-tifs`` run may have crashed mid-way, leaving
        # some images in well folders and some still loose at the top level.
        log_fn(
            f"[stage] resume: found {len(source_loose_images)} leftover "
            f"loose image(s) at top level + "
            f"{sum(1 for p in subdirs_in(input_dir) if is_valid_well_name(p.name))} "
            f"well folder(s); consuming leftovers into existing folders.\n"
        )

    # Capture zips that existed BEFORE this stage runs — we must NOT
    # delete those on a partial-zipper failure (they could be user
    # data from a prior run).
    pre_existing_zips = {p for p in _well_zips_in(input_dir)}

    # Decide whether the zipper actually needs to run: only if some loose image
    # at the top level does not already have a copy in its well folder.
    # Re-copying already-consumed images is wasted I/O, and confusing to watch.
    def _basenames_inside_well_folders() -> set[str]:
        # `subdirs_in`, not a stat per entry: in FLAT mode this sees the plate's
        # loose images, and on Windows each stat opens a file handle.
        names: set[str] = set()
        for p in subdirs_in(input_dir):
            if not is_valid_well_name(p.name):
                continue
            names.update(c.name for c in files_in(p) if is_run_input_member(c.name))
        return names

    folder_basenames = _basenames_inside_well_folders()
    unconsumed_loose = tuple(
        p for p in source_loose_images if p.name not in folder_basenames
    )
    zipper_will_run = bool(unconsumed_loose) or (
        mode is LocalInputMode.FLAT and not folder_basenames
    )
    if mode is LocalInputMode.FOLDER and not unconsumed_loose and log_fn:
        log_fn(
            "[stage] all loose image(s) already copied into well folders "
            "from a prior run; skipping zipper re-run, going straight "
            "to zip-up.\n"
        )

    # Step 1: well_plate_zipper groups loose images into per-well subfolders
    # (copied, or moved with ``--move-source-tifs``); it never writes zips.
    if zipper_will_run:
        try:
            run_zipper(
                input_dir,
                input_dir,
                log_fn=log_fn,
                progress_fn=None,
                filename_schema=filename_schema,
                filename_sep=filename_sep,
                proc_hook=proc_hook,
                move_source_tifs=move_source_tifs,
            )
        except Exception:
            # Roll back ONLY the zips we created in this aborted run.
            # Source images / folders stay intact so the user can re-run.
            for p in _well_zips_in(input_dir):
                if p not in pre_existing_zips:
                    try:
                        p.unlink()
                    except OSError:
                        pass
            raise
        grouped_names = _basenames_inside_well_folders()
        left_loose = [p.name for p in files_in(input_dir)
                      if is_run_input_member(p.name) and p.name not in grouped_names]
        if left_loose and log_fn:
            # An image the zipper could not place in a well — its name does not
            # parse under the schema — is not uploaded. It stays where it is,
            # and the success hook below will not delete it; but it must be
            # named, or a calibration frame or a mistyped file reads as part of
            # the run.
            log_fn(_named_list(
                f"[stage] WARN {len(left_loose)} image(s) at the top level are "
                f"in no well under the schema {filename_schema!r} — not "
                f"uploaded, left where they are",
                left_loose))

    # Step 2: convert each well folder into ``{well}.zip``, holding every image
    # the folder has (the pipeline's own image predicate — PNG and JPEG
    # included, AppleDouble ``._*`` sidecars and other dotfiles not).
    # From the listing's entry type, not a stat per entry. In copy mode — the
    # default — every loose image is still in input_dir when this runs, so
    # ``p.is_dir()`` here cost one stat per image at the start of every remote
    # run.
    folders_after_zipper = [
        p for p in subdirs_in(input_dir) if is_valid_well_name(p.name)]
    # The images a folder gave up are deleted the moment its own zip is
    # published, rather than at the end of the run. ``_zip_well_folder`` goes
    # through ``atomic_zip``, which renames a verified archive into place or
    # leaves nothing at all, so nothing is dropped until a complete archive
    # sits beside it — and that archive is never rewritten afterwards. Deferring
    # the deletion left the input dir holding both ``<well>/`` and
    # ``<well>.zip`` for every well for the whole run.
    #
    # What is deleted is the archived images, never the folder as a unit:
    # removing the folder took everything else in it — companion XML,
    # acquisition logs, mask subdirectories, ``.DS_Store`` — with no copy
    # anywhere. A folder that still holds something survives carrying just that
    # remainder, and the log names it. A folder with no images is left whole and
    # named — there is no archive to replace it with, and it is not uploaded —
    # and one whose well already had a user zip is left alone.
    for folder in folders_after_zipper:
        target = input_dir / f"{folder.name}.zip"
        if target.exists() and target in pre_existing_zips:
            if log_fn:
                log_fn(f"[stage] {folder.name}/ left as it is: {target.name} "
                       f"already exists and is what is uploaded for this well.\n")
            continue
        images = _zip_well_folder(folder, target)
        if not images:
            if log_fn:
                log_fn(_named_list(
                    f"[stage] WARN {folder.name}: the folder holds no images — "
                    f"well not uploaded; folder left as it is",
                    sorted(c.name for c in folder.rglob("*") if not c.is_dir())))
            continue
        outcome = replace_folder_with_archive(folder, images)
        if log_fn:
            n = len(images)
            tail = (
                "folder removed."
                if outcome.folder_removed
                else (f"folder kept, {len(outcome.kept)} file(s) not archived "
                      f"(and not uploaded): {', '.join(outcome.kept)}")
            )
            log_fn(
                f"[stage] zipped {folder.name} → {target.name} "
                f"({n} image{'s' if n != 1 else ''}); {tail}\n"
            )
    # No rollback of the zips written above. It used to unlink them all if a
    # later well failed to zip — which, now that each folder is consumed as it
    # goes, would delete the only remaining copy of every well that had already
    # succeeded. A part-zipped plate is the correct resume state anyway: the
    # next run reads the finished wells as ZIP mode and picks up the rest.

    well_zips = tuple(sorted(_well_zips_in(input_dir)))
    if not well_zips:
        raise RuntimeError(
            "The grouping step produced no per-well zips; check filename_schema and separator."
        )

    def _on_success() -> None:
        """Delete the loose top-level images this stage consumed, so the input
        dir is now pure zip mode — each only once its bytes are proven to be
        inside one of the well archives (same name, size and CRC-32).

        In copy mode those are the originals the zipper copied into the well
        folders, whose images are now in ``<well>.zip``. In move mode they are
        already gone. An image that is in no archive — a calibration frame
        whose name is not a well, a ``flatfield_reference_DAPI.tif``, a file
        the zipper refused — stays: this hook used to unlink every top-level
        TIF snapshotted before the zipper ran, archived or not.

        Well folders are not touched here at all. Each was reduced to what its
        archive does not hold at zip-up time; whatever remains in one is by
        definition not in any archive.
        """
        from services.input_resolution_service import _delete_sources_that_landed

        kept = _delete_sources_that_landed(
            source_loose_images, input_dir,
            filename_schema=filename_schema, filename_sep=filename_sep)
        if kept and log_fn:
            log_fn(_named_list(
                f"[stage] kept {len(kept)} top-level image(s) that are in no "
                f"well archive", [p.name for p in kept if p.exists()]))

    return well_zips, _on_success


def _named_list(head: str, names: list[str], limit: int = 20) -> str:
    """One log line: *head*, then the first *limit* of *names*."""
    shown = ", ".join(names[:limit])
    more = f" (and {len(names) - limit} more)" if len(names) > limit else ""
    return f"{head}: {shown}{more}\n" if names else f"{head}.\n"


def _detect_local_input_mode(input_dir: Path) -> LocalInputMode:
    """Mirror the discovery logic in process_microscopy's main(), with
    one extension over the local script: detect MIXED mode (zips +
    folders for different wells) so the staging path can resume from
    a half-completed prior run rather than silently dropping the
    unzipped wells.
    """
    zip_wells: set[str] = set()
    for p in _well_zips_in(input_dir):
        label = _canonical_well_from_path(p.stem)
        if label is not None:
            zip_wells.add(label)

    folder_wells: set[str] = set()
    # A raised OSError from the listing (e.g. the drive was yanked) propagates
    # naturally to the caller. `subdirs_in` runs before the mode is known, so on
    # a FLAT plate it sees every image — hence no stat per entry.
    for p in subdirs_in(input_dir):
        if not is_valid_well_name(p.name):
            continue
        label = _canonical_well_from_path(p.name)
        if label is None:
            continue
        # Empty folders don't count — they're often leftovers from
        # ``compress_folder_images_to_zip_and_remove`` that
        # interrupted between zip-write and folder-delete.
        try:
            if not any(p.iterdir()):
                continue
        except OSError:
            continue
        folder_wells.add(label)

    # MIXED iff there's at least one well with a folder that has no
    # matching zip AND at least one well that DOES have a zip.
    remaining = folder_wells - zip_wells
    if zip_wells and remaining:
        return LocalInputMode.MIXED
    if zip_wells:
        return LocalInputMode.ZIP
    if folder_wells:
        return LocalInputMode.FOLDER
    return LocalInputMode.FLAT


def _required_tokens(
    nuclear_token: str | None, fluor_tokens: Iterable[str]
) -> set[str]:
    """Keep the nuclear channel + every requested fluor channel."""
    keep = {t.strip() for t in fluor_tokens if t and t.strip()}
    if nuclear_token:
        keep.add(nuclear_token.strip())
    return keep


def _zip_well_folder(folder: Path, out_zip: Path) -> list[Path]:
    """Atomically zip the images directly inside ``folder`` to ``out_zip``.

    Goes through :func:`util.zip_write.atomic_zip`, so it uses the same
    ``__aw_tmp_*.pidNNN`` staging name as
    ``process_microscopy.compress_folder_images_to_zip_and_remove`` and
    publishes only a verified archive.

    "An image" is :func:`util.image_exts.is_run_input_member` — what the
    pipeline groups as input, and what ``well_plate_zipper`` put in this folder.
    This used to pack ``.tif``/``.tiff`` only, so a PNG the zipper had grouped
    sat in the folder un-uploaded with nothing said, a folder of PNGs was not
    zipped at all, and an AppleDouble ``._x.tif`` sidecar was packed and counted
    as an image.

    Returns the images that went into the archive — the paths, not a count, so
    the caller can hand them to
    :func:`util.zip_write.replace_folder_with_archive` and delete exactly what
    is now in the zip. An empty list means the folder held no images and there
    is nothing for the caller to do.

    Every image is read off the user's input volume, so the write carries the
    shared retry shield: on a network mount a dropped session mid-plate would
    otherwise fail the run outright with a bare errno.
    """
    images = sorted(p for p in files_in(folder) if is_run_input_member(p.name))
    if not images:
        return []

    def _pack() -> None:
        with atomic_zip(out_zip) as zf:
            for t in images:
                zf.write(t, arcname=t.name)

    retry_io(_pack, what=out_zip.name, advice=_TRANSFER_ADVICE, path=out_zip)
    return images


def _both(*cleanups: Callable[[], None] | None) -> Callable[[], None] | None:
    """One cleanup that runs each of *cleanups* given, or ``None``."""
    fns = [f for f in cleanups if f is not None]
    return (lambda: [f() for f in fns] and None) if fns else None


def _stage_mixed_mode(
    input_dir: Path,
    *,
    fluor_tokens: Iterable[str] | None,
    nuclear_token: str | None,
    log_fn: Callable[[str], None] | None,
) -> ZippedInput:
    """Resume from a half-completed compression: zip the leftover
    folders, combine with the existing zips, and return the union.

    v5.2.21 introduced this entry point for the MIXED input mode.
    Detection in ``_detect_local_input_mode`` already established
    that at least one well has a zip AND at least one DIFFERENT well
    has a folder of TIFs — exactly the state a network-drop
    mid-``compress_input_well_folders`` leaves behind.

    v5.2.24: the newly-zipped wells now land in ``input_dir`` itself
    (next to the existing ``{well}.zip`` files) rather than in a
    throwaway temp dir. The user can SEE the new zips, and the next
    run picks them up as plain ZIP mode without re-zipping. Source
    folders are left in place so the user can verify what was kept
    before deleting them manually — the v5.2.21 same-well
    zip+folder detection ensures the leftover folders don't trigger
    a re-zip on the next run.

    The v5.2.18 invariant still holds for the channel-filter case:
    when ``fluor_tokens`` is restricted, ALL zips (existing + new)
    are copied into a temp staging dir and filtered there. The
    originals in ``input_dir`` — including the just-written ones —
    are never mutated by the filter.
    """
    existing_zip_paths = sorted(_well_zips_in(input_dir))
    existing_wells: dict[str, Path] = {}
    for p in existing_zip_paths:
        label = _canonical_well_from_path(p.stem)
        if label is not None:
            existing_wells[label] = p

    folder_wells: dict[str, Path] = {}
    for p in subdirs_in(input_dir):
        if not is_valid_well_name(p.name):
            continue
        label = _canonical_well_from_path(p.name)
        if label is None or label in existing_wells:
            continue
        try:
            if not any(p.iterdir()):
                continue
        except OSError:
            continue
        folder_wells[label] = p

    if not folder_wells:
        # Shouldn't reach here — _detect_local_input_mode wouldn't have
        # returned MIXED. Defensive: fall back to plain ZIP mode.
        return ZippedInput(
            zip_dir=input_dir,
            mode=LocalInputMode.ZIP,
            well_zips=tuple(existing_zip_paths),
            cleanup=None,
        )

    if log_fn:
        log_fn(
            f"[stage] resume detected: {len(existing_wells)} existing "
            f"zip(s), {len(folder_wells)} leftover folder(s) to zip "
            f"({sorted(folder_wells)[:5]}{'…' if len(folder_wells) > 5 else ''}).\n"
        )

    # v5.2.24: write new zips directly into input_dir. ``_zip_well_folder``
    # uses an atomic ``__aw_tmp_*.pidNNN`` rename so a mid-run failure
    # leaves either a complete zip or no zip — never a partial. Successful
    # zips persist across a failure: the next run sees fewer leftover
    # folders and resumes faster.
    new_zip_paths: list[Path] = []
    consumed: list[Path] = []
    for label in sorted(folder_wells):
        folder = folder_wells[label]
        target = input_dir / f"{label}.zip"
        tifs = _zip_well_folder(folder, target)
        if not tifs:
            # Nothing to pack, so no archive: listing the target anyway handed
            # the upload a path that does not exist. The folder is left whole.
            if log_fn:
                log_fn(f"[stage] WARN {folder.name}: the folder holds no images — "
                       f"well not uploaded; folder left as it is.\n")
            continue
        new_zip_paths.append(target)
        # The archived TIFs go as soon as the archive is published, not at the
        # end of the run — same reasoning as the FOLDER/FLAT zip-up above. The
        # folder itself only goes when the archive covers everything in it: a
        # folder that yielded no TIFs, and one still holding non-image content,
        # both keep what they hold.
        n = len(tifs)
        tail = ""
        if tifs:
            outcome = replace_folder_with_archive(folder, tifs)
            if outcome.folder_removed:
                consumed.append(folder)
                tail = "; folder removed"
            else:
                tail = (f"; folder kept, {len(outcome.kept)} file(s) not "
                        f"archived: {', '.join(outcome.kept)}")
        if log_fn:
            log_fn(
                f"[stage] zipped {folder.name} → {target.name} "
                f"({n} image{'s' if n != 1 else ''}) — persisted to "
                f"input dir{tail}.\n"
            )

    all_zips = tuple(sorted(existing_zip_paths + new_zip_paths))

    # The consumed folders are already gone — each was removed as its archive
    # landed — so the input dir is plain ZIP mode from here on rather than
    # MIXED. This hook stays as the backstop for anything the loop above
    # declined to consume. A folder that yielded no TIFs, and one still holding
    # files the archive does not carry, are both deliberately absent from
    # ``consumed`` and so are not removed here either.
    consumed_folders = tuple(consumed)

    def _on_success() -> None:
        for folder in consumed_folders:
            try:
                shutil.rmtree(folder)
            except OSError:
                pass

    if fluor_tokens is not None:
        # v5.2.18 invariant: the channel filter must NOT mutate the user's input
        # dir, so the filtered archives are built in a staging dir. Same
        # plan-then-materialise as plain ZIP mode: decide which members survive
        # from each archive's member list, then write only those. Copying every
        # archive in full and rewriting it afterwards — which this used to do —
        # pulls the whole plate off the input volume to discard most of it,
        # which is worst exactly where MIXED mode is most likely: a resumed run
        # against a slow network share.
        plans, _unused, zero_match = _plan_staging(
            all_zips,
            keep_tokens=_required_tokens(nuclear_token, fluor_tokens),
            log_fn=log_fn,
        )
        _raise_on_zero_channel_match(zero_match)
        staging = _open_staging_if_needed(plans, log_fn=log_fn)
        try:
            staged_zips = _materialize_staging(plans, staging, log_fn=log_fn)
            return ZippedInput(
                zip_dir=staging or input_dir,
                mode=LocalInputMode.MIXED,
                well_zips=staged_zips,
                cleanup=(None if staging is None else
                         (lambda d=staging: shutil.rmtree(d, ignore_errors=True))),
                on_pipeline_success=_on_success,
            )
        except Exception:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise

    # Non-filter path: zips live in input_dir, no cleanup needed.
    return ZippedInput(
        zip_dir=input_dir,
        mode=LocalInputMode.MIXED,
        well_zips=all_zips,
        cleanup=None,
        on_pipeline_success=_on_success,
    )


@dataclass(frozen=True)
class _StagePlan:
    """What of one source archive needs to reach the upload staging dir.

    ``keep`` is ``None`` when every member survives (the archive is taken
    wholesale) and a member-name tuple otherwise. Deciding this BEFORE any bytes
    move is the whole point: a zip's member list comes from its central
    directory — a small read at the end of the file — so planning the entire
    plate costs a handful of seeks even when the archives live on a NAS, and the
    materialise pass then reads only the members that are actually going to be
    uploaded.
    """

    src: Path
    keep: tuple[str, ...] | None
    n_members: int
    note: str = ""


def _drop_reason(dropped_channel: list[str], dropped_done: int,
                 filename_schema: str, filename_sep: str) -> str:
    """Say *what* the planner dropped, in the words the user can act on.

    The log used to report only that an archive was being rewritten. That is the
    one thing the user can already see happening; what they cannot see is which
    channel caused it — and "all my channels are selected, so why is it copying
    my plate?" is not answerable from any other line the run emits.

    Channels are named by the filename's own ``channel`` slot under the run's
    schema. A member whose name does not decompose under that schema is reported
    by its filename instead: "this file's name means nothing to the schema you
    gave" is itself the answer often enough to be worth saying plainly, and a
    guessed channel token would be worse than none.
    """
    parts: list[str] = []
    if dropped_channel:
        fields = [f.strip().lower() for f in (filename_schema or "").split(":")]
        ci = fields.index("channel") if "channel" in fields else -1
        counts: dict[str, int] = {}
        for member in dropped_channel:
            stem = Path(member).stem
            tokens = stem.split(filename_sep)
            token = tokens[ci] if 0 <= ci < len(tokens) else ""
            counts[token or Path(member).name] = counts.get(
                token or Path(member).name, 0) + 1
        named = ", ".join(f"{tok} ({n})" for tok, n in
                          sorted(counts.items(), key=lambda kv: -kv[1]))
        parts.append(f"no channel match: {named}")
    if dropped_done:
        parts.append(f"already processed: {dropped_done} FOV-group(s)")
    return "; ".join(parts) if parts else "reason not recorded"


def _raise_on_zero_channel_match(zero_match: list[str]) -> None:
    """Fail naming every well whose channel filter matched nothing.

    A filter that matches zero images is nearly always a mistyped channel name,
    and learning that one well at a time is useless — so the plan pass collects
    every offender and this raises once, before any bytes have moved.
    """
    if not zero_match:
        return
    sample = ", ".join(zero_match[:5])
    more = f" (and {len(zero_match) - 5} more)" if len(zero_match) > 5 else ""
    raise RuntimeError(
        f"Channel filter matched zero images in {len(zero_match)} well(s): "
        f"{sample}{more}. Likely a typo in the channel name(s) on the Analyze "
        f"tab — check your nuclear and fluor channel tokens against the "
        f"filenames inside the well zips."
    )


def _plan_staging(
    well_zips: tuple[Path, ...],
    *,
    keep_tokens: set[str] | None,
    incremental: bool = False,
    prior_output_dir: Path | None = None,
    csv_prefix: str = "measurements",
    filename_schema: str = "",
    filename_sep: str = "_",
    force_wells: frozenset[str] = frozenset(),
    background_wells: frozenset[str] = frozenset(),
    log_fn: Callable[[str], None] | None = None,
) -> tuple[list[_StagePlan], list[str], list[str]]:
    """Decide, per well, which members need staging — reading no member bytes.

    Returns ``(plans, up_to_date_wells, zero_channel_match_wells)``. A well whose
    every member is already processed contributes nothing to *plans*; a well
    whose channel filter matches nothing is named in the third list so the caller
    can fail with every offender at once (a filter matching zero images is nearly
    always a typo'd channel name, and finding out one well at a time is useless).
    """
    plans: list[_StagePlan] = []
    up_to_date: list[str] = []
    zero_match: list[str] = []
    n_total = len(well_zips)

    for idx, wz in enumerate(well_zips, start=1):
        try:
            with zipfile.ZipFile(wz, "r") as zf:
                # ``infolist`` over ``namelist``: the compressed size of every
                # member comes from the same central-directory read, at no extra
                # I/O, and it is what decides below whether rewriting this
                # archive is worth more than it costs.
                infos = zf.infolist()
                members = [i.filename for i in infos]
                sizes = {i.filename: i.compress_size for i in infos}
        except (OSError, zipfile.BadZipFile) as exc:
            # Unreadable here means unreadable later too, but staging it whole
            # keeps the previous behaviour: the remote gets the archive and
            # reports the real error against the data, not against our planning.
            if log_fn:
                log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                       f"WARN unreadable ({exc.__class__.__name__}: {exc}); "
                       f"staging whole\n")
            plans.append(_StagePlan(src=wz, keep=None, n_members=0,
                                    note="unreadable"))
            continue

        if not members:
            if log_fn:
                log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                       f"WARN empty zip; skipping\n")
            continue

        # Only the images are the run's business. A zip written on macOS carries
        # `.DS_Store`; one written by some other tool carries directory entries
        # or metadata sidecars. None of those is a channel, so the channel filter
        # has no opinion on them — and letting it drop them made every archive on
        # the plate look changed (276 of 279 members "kept"), which forced a full
        # rewrite of the whole dataset to remove a few hundred bytes per well.
        # The remote pipeline ignores them anyway: `find_image_groups_in_zip`
        # skips dotfiles and anything without an image extension.
        images = [m for m in members if _is_image_member(m)]
        keep = list(images)
        dropped_channel: list[str] = []
        dropped_done = 0
        if keep_tokens:
            keep = [m for m in keep if _member_matches(m, keep_tokens)]
            dropped_channel = [m for m in images if m not in set(keep)]
            if images and not keep:
                zero_match.append(wz.stem)
                if log_fn:
                    log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                           f"WARN zero channel matches; will fail after pass\n")
                continue

        well = _canonical_well_from_path(wz.stem)
        if _bg_well_key(wz.stem) in background_wells:
            # An empty-well blank is *input to the correction*, not a well whose
            # own output is wanted, so no filter may drop it or thin it out. An
            # incremental re-run finds every one of its FOVs already processed
            # and would otherwise skip it entirely — which starves every well on
            # the plate of the correction the run asked for, and a partial
            # rewrite would be worse still: the field would be estimated from a
            # fraction of the blank while the log reported it as built.
            if log_fn:
                log_fn(f"[remote] ⊙ {wz.stem}: empty-well reference — staged "
                       f"whole, not diffed\n")
            plans.append(_StagePlan(src=wz, keep=None, n_members=len(images)))
            continue
        if well is not None and well in force_wells:
            # Asked for in full, so the incremental diff below is skipped
            # outright. Without this the split drops the well before a single
            # byte is uploaded and the remote never sees it — so
            # ``--force_wells`` on the remote command line has nothing to act
            # on, and the well that most needs reprocessing is the one
            # guaranteed not to be. Logged, because "up to date — not uploaded"
            # for the well the user just forced is exactly the confusion this
            # avoids.
            if log_fn:
                log_fn(f"[remote] ↻ {well}: forced — uploading in full\n")
            plans.append(_StagePlan(src=wz, keep=None, n_members=len(images)))
            continue
        if incremental and prior_output_dir is not None and well is not None:
            # CSV is the primary done-set; fall back to the well's local output
            # zip so a missing / lost CSV doesn't re-upload already-done FOVs.
            done = read_done_fov_keys(prior_output_dir / f"{csv_prefix}_{well}.csv")
            done |= read_done_fov_keys_from_zip(
                prior_output_dir / f"{well}_out.zip",
                schema=filename_schema, sep=filename_sep)
            if done:
                before = len(keep)
                keep = [
                    m for m in keep
                    if fov_tp_from_name(m, schema=filename_schema,
                                        sep=filename_sep) not in done
                ]
                dropped_done = before - len(keep)
                if images and not keep:
                    up_to_date.append(well)
                    if log_fn:
                        log_fn(f"[remote] ✓ {well}: up to date (local) — "
                               f"not uploaded\n")
                    continue

        # "Changed" is decided on the images alone, so a stray `.DS_Store` can
        # never be the reason an archive is rebuilt. When nothing is dropped the
        # archive is used exactly as it is, junk members and all; when something
        # is, the rewrite carries only the images the remote will actually read.
        take_all = len(keep) == len(images)
        if not take_all:
            # Rewriting costs a full local read and write of this archive before
            # a byte is uploaded, and it buys a smaller transfer. Below a
            # worthwhile saving that trade is plainly bad — the remote ignores
            # channels it was not asked for, so sending them costs bandwidth and
            # nothing else. Decided on compressed bytes, which is what crosses
            # the wire, not on member counts: two frames out of twelve can be
            # 40% of an archive or 0.4% of it.
            dropped = [m for m in images if m not in set(keep)]
            drop_bytes = sum(sizes.get(m, 0) for m in dropped)
            total_bytes = sum(sizes.get(m, 0) for m in images)
            saving = (drop_bytes / total_bytes) if total_bytes else 0.0
            if saving < REWRITE_MIN_SAVING:
                take_all = True
                if log_fn:
                    log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                           f"{len(dropped)} of {len(images)} image(s) could be "
                           f"dropped, worth {saving * 100:.1f}% of "
                           f"{_fmt_bytes(total_bytes)} — uploading it as it is "
                           f"rather than rewriting it to save that.\n")
            elif log_fn:
                log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                       f"rewriting, {len(dropped)} of {len(images)} image(s) "
                       f"dropped ({saving * 100:.0f}% of "
                       f"{_fmt_bytes(total_bytes)}): "
                       f"{_drop_reason(dropped_channel, dropped_done, filename_schema, filename_sep)}\n")
        plans.append(_StagePlan(
            src=wz, keep=None if take_all else tuple(keep),
            n_members=len(images),
        ))
    return plans, up_to_date, zero_match


def _open_staging_if_needed(
    plans: list[_StagePlan], *, log_fn: Callable[[str], None] | None = None,
) -> Path | None:
    """A staging dir, but only if some archive actually has to be rewritten.

    Staging exists for exactly one reason: an archive the run has to rewrite —
    to drop unwanted channels, or the FOV-groups an incremental run has already
    processed — must not be written back over the user's input. An archive that
    survives its plan intact is not rewritten at all, so putting it in staging
    would move the same bytes twice: once onto the local scratch volume, then
    again to the remote. Those upload straight from where they already are.

    That is the common case, not an edge one. The Analyze tab always asks for
    selective channels, so a run whose channel list happens to cover every image
    in the plate takes the filtered path with a filter that drops nothing — and
    used to copy the whole plate into the OS temp dir to achieve it. Returning
    ``None`` here is what makes that run upload directly from the source.

    When it *is* opened it sits beside the input archives rather than on the
    home volume: what it holds is a rewritten copy of those archives, so the
    volume that already has room for the originals is the one likeliest to have
    room for the copies — and the home volume, which a remote run otherwise has
    no reason to touch, stays out of it.
    """
    rewrites = sum(1 for p in plans if p.keep is not None)
    if log_fn is not None and rewrites < len(plans):
        log_fn(f"[remote] {len(plans) - rewrites} archive(s) need no rewrite — "
               f"uploading those straight from the input, nothing copied "
               f"locally\n")
    if not rewrites:
        return None
    near = next((p.src.parent for p in plans if p.keep is not None), None)
    if near is None:                       # unreachable: rewrites > 0
        return _new_scratch_dir("allwell-stage-")
    return _new_transit_dir(near, "allwell-stage-", log_fn=log_fn)


#: How much of one member is held in memory at a time while it is copied into
#: a staged archive.
_COPY_CHUNK = 1 << 20


def _write_kept_members(plan: _StagePlan, dest: Path) -> None:
    """Write *plan*'s kept members from its source archive into *dest*.

    **Streamed**, a chunk at a time: ``writestr(info, src.read(member))`` held
    each member whole in memory, and a single image in a stitched or
    multi-page acquisition is hundreds of megabytes.

    **Deterministic**, so a retry of an unchanged plate produces the same
    archive: each member keeps its source ``ZipInfo`` (name, timestamp,
    compression), so the same members of the same source compress to the same
    bytes, and *dest*'s modification time is then set to the source archive's.
    The upload cache key (``services.remote_scratch.fingerprint_archives``)
    and through it the ``run_id`` are derived from each staged archive's name,
    size, whole-second mtime and member list, so a staged copy stamped with
    the time it was written gave every attempt a new key, a new remote dataset
    directory and a full re-upload — and left the previous attempt's copy on
    the remote. A changed source image changes the source archive, whose
    mtime and member CRCs then move the key, which is what the key is for.
    """
    with atomic_zip(dest) as dst:
        with zipfile.ZipFile(plan.src, "r") as src:
            for member in plan.keep or ():
                info = src.getinfo(member)
                out = copy.copy(info)
                with src.open(info) as r, dst.open(out, "w") as w:
                    shutil.copyfileobj(r, w, _COPY_CHUNK)
    st = plan.src.stat()
    os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns))


def _materialize_staging(
    plans: list[_StagePlan],
    staging: Path | None,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[Path, ...]:
    """Turn the plans into the list of archives to upload.

    A plan that keeps every member is not written anywhere: its source path goes
    into the returned list unchanged and rsync reads it from where it already
    lives. Copying it into scratch first would move the same bytes twice — once
    onto the local scratch volume, once to the remote — and the second copy is
    the only one anybody needs. *staging* is ``None`` when no plan needs a
    rewrite at all, which is the whole-plate case.

    A filtered plan is written member by member straight from the source into
    *staging*, so the members it drops are never read. That is what matters when
    the source is a network volume: an earlier shape copied every archive in
    full and only then rewrote it down to the members it wanted, so a run
    uploading a few hundred megabytes still dragged the whole plate across the
    network first.
    """
    staged: list[Path] = []
    n_total = len(plans)
    kept_members = 0
    total_members = 0
    passed_through = 0
    for idx, plan in enumerate(plans, start=1):
        total_members += plan.n_members
        if plan.keep is None:
            # Nothing to rewrite — upload it from where it is.
            kept_members += plan.n_members
            passed_through += 1
            staged.append(plan.src)
            continue
        if staging is None:                     # unreachable via the helper
            raise RuntimeError(
                f"{plan.src.name} needs rewriting but no staging dir was opened")
        dest = staging / plan.src.name
        t0 = time.monotonic()

        retry_io(
            lambda _plan=plan, _dest=dest: _write_kept_members(_plan, _dest),
            what=f"{plan.src.name} into the upload staging dir",
            cleanup=lambda d=dest: d.unlink(missing_ok=True),
            advice=_TRANSFER_ADVICE,
            log_fn=log_fn,
            path=dest,
        )
        kept_members += len(plan.keep)
        if log_fn:
            mb = dest.stat().st_size / (1024 * 1024) if dest.exists() else 0.0
            log_fn(f"[remote] staged ({idx}/{n_total}): {plan.src.stem} → "
                   f"{len(plan.keep)}/{plan.n_members} member(s), "
                   f"{mb:.1f} MB in {_fmt_secs(time.monotonic() - t0)}\n")
        staged.append(dest)
    if log_fn and total_members and kept_members < total_members:
        log_fn(f"[remote] staging: summary — {kept_members}/{total_members} "
               f"member(s) across {n_total} well(s); the rest were never read "
               f"({passed_through} archive(s) uploaded from the input as-is).\n")
    return tuple(staged)


# Shared with the pipeline (`process_microscopy._GROUP_IMAGE_EXTS`) rather
# than restated here. These two lists disagreed: this one omitted .png /
# .jpg / .jpeg, so such a member was invisible to the planner and was
# silently dropped from any archive rewritten for another reason — input
# the remote would have processed, gone with nothing said.
_IMAGE_MEMBER_EXTS = tuple(sorted(GROUP_IMAGE_EXTS))


def _is_image_member(member: str) -> bool:
    """Whether a zip member is one of the images the run is here to process.

    Everything else in an archive — `.DS_Store`, directory entries, metadata
    sidecars — is not a channel, so the channel filter must have no opinion on
    it. Deciding "did this archive change?" on the images alone is what keeps a
    single stray file from forcing a rewrite of the whole plate. Mirrors what
    ``process_microscopy.find_image_groups_in_zip`` already skips on the remote
    side: a leading dot, or a suffix that is not an image.
    """
    base = Path(member).name
    if not base or base.startswith("."):
        return False
    return base.lower().endswith(_IMAGE_MEMBER_EXTS)


def _member_matches(member: str, keep_tokens: set[str]) -> bool:
    """A zip member is kept if any keep_token is a substring of its
    basename (matches the same loose-token discipline process_microscopy
    uses when grouping channels)."""
    base = Path(member).name
    return any(tok in base for tok in keep_tokens)


def _filter_new_fovs_in_staging(
    well_zips: tuple[Path, ...],
    *,
    prior_output_dir: Path,
    csv_prefix: str,
    filename_schema: str,
    filename_sep: str,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """**Superseded and unused in production** — see :func:`_plan_staging`.

    The live remote path plans from each archive's central directory and never
    copies an archive it does not have to rewrite; this earlier shape copied
    every archive into scratch first, which dragged a whole NAS-resident plate
    across the network before a byte went to the remote. Only
    ``tests/test_remote_incremental.py`` still calls it. Anything changed about
    the incremental split has to change ``_plan_staging`` to reach a real run —
    fixing this one alone looks right and does nothing.

    Client-side incremental split: rewrite each staged well zip to keep only
    members whose ``(fov, timepoint)`` is NOT already in the well's local CSV,
    and drop wells whose every input FOV is already done.

    The remote then processes only genuinely-new FOVs (no remote-side diff), and
    the partial outputs are merged back locally on fetch. Operates on the staged
    copies only — never the user's originals. Returns
    ``(surviving_well_zips, up_to_date_wells)``.
    """
    survivors: list[Path] = []
    up_to_date: list[str] = []
    n_total = len(well_zips)

    for idx, wz in enumerate(well_zips, start=1):
        well = _canonical_well_from_path(wz.stem)
        if well is None:
            survivors.append(wz)
            continue
        # CSV is the primary done-set; fall back to the well's local output zip
        # so a missing/lost CSV doesn't make us re-upload already-done FOVs for a
        # full remote re-segmentation ("reanalyzed previously processed images").
        # The (fov, tp) key readers are the shared stdlib-only helpers in
        # pipeline.incremental_keys (audit A2) — no imaging-stack import.
        done = read_done_fov_keys(prior_output_dir / f"{csv_prefix}_{well}.csv")
        done |= read_done_fov_keys_from_zip(
            prior_output_dir / f"{well}_out.zip",
            schema=filename_schema, sep=filename_sep)
        if not done:
            survivors.append(wz)  # genuinely new well → upload in full
            continue
        try:
            with zipfile.ZipFile(wz, "r") as zf:
                members = zf.namelist()
        except (OSError, zipfile.BadZipFile) as exc:
            # A corrupt input zip is uploaded whole rather than incrementally —
            # name it so the user can tell why a "done" well re-uploads in full
            # (audit E11).
            if log_fn:
                log_fn(f"[remote] WARN {well}: input zip unreadable ({exc}); "
                       f"uploading in full — {wz.name}\n")
            survivors.append(wz)
            continue
        keep = [
            m for m in members
            if fov_tp_from_name(m, schema=filename_schema, sep=filename_sep) not in done
        ]
        if not keep:
            # Every input FOV already processed locally — skip the upload.
            try:
                wz.unlink()
            except OSError:
                pass
            up_to_date.append(well)
            if log_fn:
                log_fn(f"[remote] ✓ {well}: up to date (local) — not uploaded\n")
            continue
        if len(keep) == len(members):
            survivors.append(wz)  # nothing of this well's input is done yet
            continue
        # Read handle closed before publication — see the filter pass above.
        with atomic_zip(wz) as dst:
            with zipfile.ZipFile(wz, "r") as src:
                for m in keep:
                    dst.writestr(src.getinfo(m), src.read(m))
        survivors.append(wz)
        if log_fn:
            log_fn(
                f"[remote] split ({idx}/{n_total}): {well} → uploading "
                f"{len(keep)}/{len(members)} new-FOV file(s)\n"
            )
    return tuple(survivors), tuple(up_to_date)


def input_well_containers(input_dir: Path | None) -> dict[str, Path]:
    """``{well: path}`` for the well archives and folders in an input directory.

    "Which entries in this folder are wells?" is a question three callers ask —
    the client-side completeness check, the pre-run coverage scan, and anything
    that follows them — and it was being answered by a copied loop each time.
    A copy that drifts here does not raise: it changes the *denominator* the
    caller measures against, which surfaces as a coverage fraction nobody can
    reproduce.

    Dot- and underscore-prefixed entries are skipped, which is what keeps
    ``.aw_journal/`` and the transit directories out of a plate's well count.
    Unreadable directory: ``{}``, so a caller measures nothing rather than
    measuring wrong.
    """
    if input_dir is None:
        return {}
    out: dict[str, Path] = {}
    try:
        for path in sorted(Path(input_dir).iterdir()):
            if path.name.startswith(".") or path.name.startswith("_"):
                continue
            stem = path.stem if path.suffix == ".zip" else path.name
            label = _canonical_well_label(stem)
            if label and (path.is_dir() or path.suffix == ".zip"):
                out[label] = path
    except OSError:
        return {}
    return out
