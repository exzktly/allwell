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
through removes it before the error propagates. Folder and loose-TIF plates
are zipped into the input directory itself, deliberately, so the next run
finds zips and skips the zip-up.
"""

from __future__ import annotations
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
from util.image_exts import GROUP_IMAGE_EXTS
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
) -> ZippedInput:
    """Return a directory of per-well zips for ``input_dir``.

    For inputs that are already ``{well}.zip``, this is a no-op pointer.
    For loose-TIF or folder layouts, runs the existing
    ``well_plate_zipper`` (re-used via :mod:`services.input_resolution_service`)
    into a temp directory and returns that.

    Phase-3 selective staging: when ``fluor_tokens`` is restricted, the
    selective filter rewrites each well's zip to keep only the requested
    channels — cutting upload by the unused-channel fraction. The nuclear
    token is always kept (segmentation needs it).
    """
    from services.input_resolution_service import run_zipper

    input_dir = input_dir.resolve()
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
        # Opened only if something actually has to be rewritten; when the
        # filter drops nothing from any archive there is no local copy at all.
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
                up_to_date_wells=tuple(up_to_date_list))
        except Exception:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise

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
        return _then_split(_stage_mixed_mode(
            input_dir, fluor_tokens=fluor_tokens, nuclear_token=nuclear_token,
            log_fn=log_fn))

    # Folder or flat mode: shell out to well_plate_zipper. The zipper writes
    # ``{well}.zip`` files directly into ``input_dir`` (alongside the
    # source loose TIFs / well folders). Loose TIFs at the top level persist
    # through the run so a failure can be re-tried from scratch — a well
    # folder's archived TIFs do not, see below, where each is removed the
    # moment its own zip is published; ``on_pipeline_success`` deletes the
    # remaining sources on clean completion so the
    # input dir converts from loose-TIF / folder mode to plain ZIP mode
    # for future runs (next ``_detect_local_input_mode`` call returns
    # ``ZIP`` and the zipper is skipped). This replaces the old behavior
    # of staging into ``tempfile.mkdtemp(prefix="allwell-zips-")``,
    # which (1) ate temp-dir space on machines with small /tmp, and
    # (2) discarded all zips at run end, forcing a full re-zip on
    # every re-run.
    #
    # Snapshot the source artifacts BEFORE running the zipper so the
    # post-success deletion sees exactly the set the user started with,
    # not the post-zipper state where the zipper's own output sits
    # alongside the originals.
    source_loose_tifs: tuple[Path, ...] = tuple()
    source_well_folders: tuple[Path, ...] = tuple()
    if mode is LocalInputMode.FLAT:
        source_loose_tifs = tuple(sorted(
            p for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF")
            for p in input_dir.glob(pat)
        ))
    elif mode is LocalInputMode.FOLDER:
        source_well_folders = tuple(
            p for p in subdirs_in(input_dir) if is_valid_well_name(p.name))
        # v5.x.x resume support: a prior ``--move-source-tifs`` run
        # may have crashed mid-way, leaving SOME TIFs in well
        # subfolders and SOME still loose at the top level. Capture
        # the leftovers — they go into the same snapshot as the
        # fresh-FLAT case so the post-success hook deletes them too
        # (or, for move-mode, the zipper itself will have consumed
        # them by run end and the unlink is a no-op caught by the
        # OSError handler).
        source_loose_tifs = tuple(sorted(
            p for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF")
            for p in input_dir.glob(pat)
        ))
        if source_loose_tifs and log_fn:
            log_fn(
                f"[stage] resume: found {len(source_loose_tifs)} leftover "
                f"loose TIF(s) at top level + {len(source_well_folders)} "
                f"well folder(s); consuming leftovers into existing folders.\n"
            )

    # Capture zips that existed BEFORE this stage runs — we must NOT
    # delete those on a partial-zipper failure (they could be user
    # data from a prior run).
    pre_existing_zips = {p for p in _well_zips_in(input_dir)}

    # Step 0 (v5.x.x): decide whether the zipper actually needs to
    # run. The framework gets here for FLAT mode (loose TIFs at top
    # level), fresh FOLDER mode (well subfolders), and the resume
    # cases (partial move, post-copy crash). The zipper only has work
    # to do if there are loose TIFs at top level that DON'T already
    # have a copy in their target well subfolder — i.e. truly
    # unconsumed sources. Re-copying already-consumed TIFs is just
    # wasted I/O (and on a copy-mode-after-crash retry, also produces
    # confusing logs as the user watches the zipper "do work" against
    # files that are already in their folders).
    def _basenames_inside_well_folders() -> set[str]:
        # `subdirs_in`, not a stat per entry: in FLAT mode this sees the plate's
        # loose images, and on Windows each stat opens a file handle.
        names: set[str] = set()
        for p in subdirs_in(input_dir):
            if not is_valid_well_name(p.name):
                continue
            names.update(c.name for c in files_in(p)
                         if c.suffix.lower() in (".tif", ".tiff"))
        return names

    folder_basenames = _basenames_inside_well_folders()
    unconsumed_loose_tifs = tuple(
        p for p in source_loose_tifs if p.name not in folder_basenames
    )
    zipper_will_run = bool(unconsumed_loose_tifs) or (
        mode is LocalInputMode.FLAT and not folder_basenames
    )
    if mode is LocalInputMode.FOLDER and not unconsumed_loose_tifs and log_fn:
        log_fn(
            "[stage] all loose TIF(s) already copied into well folders "
            "from a prior run; skipping zipper re-run, going straight "
            "to zip-up.\n"
        )

    # Step 1: well_plate_zipper groups loose TIFs into per-well
    # subfolders (the v5.x.x ``--move-source-tifs`` flag and copy
    # default both produce folders, never zips — the zip-up step is
    # separate and lives in ``_zip_well_folder`` below). Skipped when
    # the prior-run detection above shows every loose TIF already has
    # a folder copy.
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
            # Source TIFs / folders stay intact so the user can re-run.
            for p in _well_zips_in(input_dir):
                if p not in pre_existing_zips:
                    try:
                        p.unlink()
                    except OSError:
                        pass
            raise

    # Step 2: convert each well folder into ``{well}.zip``. This is
    # the same atomic-tmp + rename pattern that ``_stage_mixed_mode``
    # uses for its leftover-folder zip-up. Without this step the
    # remote pipeline would have nothing to upload — pre-v5.x.x the
    # FLAT / FOLDER path through ``_stage_input_zips`` was broken
    # because the framework expected zips here but the zipper only
    # produces folders.
    # From the listing's entry type, not a stat per entry. In copy mode — the
    # default — every loose TIF is still in input_dir when this runs, so
    # ``p.is_dir()`` here cost one stat per image at the start of every remote
    # run: the defect the site three lines up was converted for.
    folders_after_zipper = [
        p for p in subdirs_in(input_dir) if is_valid_well_name(p.name)]
    # The TIFs a folder gave up are deleted the moment its own zip is
    # published, rather than at the end of the run. ``_zip_well_folder`` goes
    # through ``atomic_zip``, which renames a verified archive into place or
    # leaves nothing at all, so nothing is dropped until a complete archive
    # sits beside it. Deferring the deletion left the input dir holding both
    # ``<well>/`` and ``<well>.zip`` for every well for the whole run — two full
    # copies of the plate on the user's volume at peak, on exactly the runs
    # where the input is largest.
    #
    # What is deleted is the archived TIFs, never the folder as a unit: this
    # archive holds ``.tif``/``.tiff`` only, and removing the folder took
    # everything else in it — companion XML, acquisition logs, mask
    # subdirectories, ``.DS_Store`` — with no copy anywhere. A folder that
    # still holds something survives carrying just that remainder, and the log
    # names it rather than reporting the folder gone. A folder with no TIFs is
    # left whole (there is no archive to replace it with), and one whose well
    # already had a user zip is skipped and likewise left.
    preserved: set[Path] = set()
    for folder in folders_after_zipper:
        target = input_dir / f"{folder.name}.zip"
        if target.exists() and target in pre_existing_zips:
            # User-data zip from a prior run; leave it and its folder alone.
            continue
        tifs = _zip_well_folder(folder, target)
        if not tifs:
            continue
        outcome = replace_folder_with_archive(folder, tifs)
        if not outcome.folder_removed:
            preserved.add(folder)
        if log_fn:
            n = len(tifs)
            tail = (
                "folder removed."
                if outcome.folder_removed
                else (f"folder kept, {len(outcome.kept)} file(s) not archived: "
                      f"{', '.join(outcome.kept)}")
            )
            log_fn(
                f"[stage] zipped {folder.name} → {target.name} "
                f"({n} TIF{'s' if n != 1 else ''}); {tail}\n"
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

    # The post-success hook needs to delete every well folder the zip-up step
    # consumed (both the snapshotted ones for FOLDER mode and the freshly
    # created ones for FLAT mode). Re-scan now rather than using the pre-zipper
    # snapshot because FLAT-mode folders didn't exist when we took the
    # snapshot — but skip the folders the pass deliberately preserved, or this
    # hook would destroy the very remainder that pass kept.
    folders_to_clean = tuple(p for p in folders_after_zipper if p not in preserved)

    def _on_success() -> None:
        """Delete the source artifacts this stage consumed so the
        input dir is now pure zip mode:

        * loose top-level TIFs (snapshotted before the zipper ran —
          in copy mode they're the originals we need to remove; in
          move mode they're already gone and the unlink is a no-op
          caught by the OSError handler);
        * every well folder we zipped up whose contents the archive fully
          covers (whether it pre-existed for FOLDER mode or the zipper just
          created it for FLAT mode). A folder holding files the archive does
          not carry is not in this list at all.
        """
        for tif in source_loose_tifs:
            try:
                tif.unlink()
            except OSError:
                pass
        for folder in folders_to_clean:
            try:
                shutil.rmtree(folder)
            except OSError:
                pass

    # Even in ``move_source_tifs`` mode the post-success hook still
    # has work to do (delete the well folders we just zipped up); only
    # the loose-TIF unlinks become no-ops. Don't short-circuit.
    _on_success_effective: Callable[[], None] | None = _on_success

    if fluor_tokens is not None:
        # Filter modifies zips in place. Safe here because the zips
        # we just wrote are scratch artifacts (the user's "real" data
        # is the loose TIFs / folders we snapshotted above, still
        # intact). The v5.2.18 "don't mutate user's input" invariant
        # is preserved by treating the loose sources as the canonical
        # form until the run succeeds.
        zin = _filter_channels_in_staging(
            input_dir, well_zips, mode,
            keep_tokens=_required_tokens(nuclear_token, fluor_tokens),
            log_fn=log_fn,
        )
        zin.on_pipeline_success = _on_success_effective
        return _then_split(zin)

    return _then_split(ZippedInput(zip_dir=input_dir, mode=mode, well_zips=well_zips,
                                   cleanup=None, on_pipeline_success=_on_success_effective))


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
    """Atomically zip the TIFs in ``folder`` to ``out_zip``.

    Goes through :func:`util.zip_write.atomic_zip`, so it uses the same
    ``__aw_tmp_*.pidNNN`` staging name as
    ``process_microscopy.compress_folder_images_to_zip_and_remove`` and
    publishes only a verified archive.

    Returns the TIFs that went into the archive — the paths, not a count, so
    the caller can hand them to
    :func:`util.zip_write.replace_folder_with_archive` and delete exactly what
    is now in the zip. An empty list means the folder held no images and there
    is nothing for the caller to do.

    Every TIF is read off the user's input volume, so the write carries the
    shared retry shield: on a network mount a dropped session mid-plate would
    otherwise fail the run outright with a bare errno.
    """
    tifs = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in (".tif", ".tiff")
    )
    if not tifs:
        return []

    def _pack() -> None:
        with atomic_zip(out_zip) as zf:
            for t in tifs:
                zf.write(t, arcname=t.name)

    retry_io(_pack, what=out_zip.name, advice=_TRANSFER_ADVICE, path=out_zip)
    return tifs


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
                f"({n} TIF{'s' if n != 1 else ''}) — persisted to "
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

        def _write(_plan=plan, _dest=dest) -> None:
            with atomic_zip(_dest) as dst:
                with zipfile.ZipFile(_plan.src, "r") as src:
                    for member in _plan.keep or ():
                        dst.writestr(src.getinfo(member), src.read(member))

        retry_io(
            _write,
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


def _filter_channels_in_staging(
    zip_dir: Path,
    well_zips: tuple[Path, ...],
    mode: LocalInputMode,
    *,
    keep_tokens: set[str],
    log_fn: Callable[[str], None] | None,
) -> ZippedInput:
    """Rewrite each well zip to drop members whose name doesn't contain one
    of ``keep_tokens``.

    v5.2.18: renamed from ``_filter_channels_in_place``. The caller is
    expected to have already copied the user's zips into a staging
    temp dir — this function still rewrites in place RELATIVE to the
    files it's handed, so passing it the user's original input dir
    would corrupt the user's dataset (a pre-v5.2.18 bug).

    Phase-3 selective staging: this is the lever-1 multiplier — for a
    plate with 4 channels but only 2 in use, this cuts upload in half.
    Idempotent: a zip whose members are already filtered is a no-op.

    v5.2.20: emits a per-well log line so the user sees a steady stream
    of progress during a 96-well filter rather than wondering whether
    the GUI has hung. No-op wells (everything already matches) get an
    ``all N kept (no-op)`` line; rewritten wells report before/after
    size and elapsed time. The end-of-plate summary fires only when at
    least one well was actually rewritten.
    """
    if not keep_tokens:
        return ZippedInput(zip_dir=zip_dir, mode=mode, well_zips=well_zips, cleanup=None)

    n_total = len(well_zips)
    total_in = 0
    total_kept = 0
    rewrote = 0
    empty_wells: list[str] = []  # v5.2.18 typo-guard accumulator
    for idx, wz in enumerate(well_zips, start=1):
        try:
            with zipfile.ZipFile(wz, "r") as zf:
                members = zf.namelist()
            keep_mask = [_member_matches(m, keep_tokens) for m in members]
        except (OSError, zipfile.BadZipFile) as exc:
            if log_fn:
                log_fn(
                    f"[remote] filter ({idx}/{n_total}): {wz.stem} → "
                    f"WARN unreadable ({exc.__class__.__name__}: {exc}); skipping\n"
                )
            continue
        # Empty zip — nothing to filter, but distinct from "all kept".
        # all([]) is True in Python, which would otherwise silently
        # bucket this into the no-op branch with a misleading
        # "all 0 kept" line.
        if not members:
            if log_fn:
                log_fn(
                    f"[remote] filter ({idx}/{n_total}): {wz.stem} → "
                    f"WARN empty zip; skipping\n"
                )
            continue
        kept = sum(keep_mask)
        total_in += len(members)
        total_kept += kept
        # v5.2.18 typo guard: if the requested tokens match ZERO
        # images in a well, that's almost always a typo'd channel
        # name. Collect the affected wells and raise after the loop
        # so we can name them all at once.
        if kept == 0:
            empty_wells.append(wz.stem)
            if log_fn:
                log_fn(
                    f"[remote] filter ({idx}/{n_total}): {wz.stem} → "
                    f"WARN zero matches; will fail after pass\n"
                )
            continue
        if kept == len(members):
            if log_fn:
                log_fn(
                    f"[remote] filter ({idx}/{n_total}): {wz.stem} → "
                    f"all {kept} kept (no-op)\n"
                )
            continue
        # Re-write the zip with only the kept members.
        t0 = time.monotonic()
        size_before = wz.stat().st_size
        # The read handle is closed inside the atomic_zip block: publication
        # renames over `wz`, which Windows refuses while a handle is open.
        with atomic_zip(wz) as dst:
            with zipfile.ZipFile(wz, "r") as src:
                for member, keep in zip(members, keep_mask):
                    if keep:
                        dst.writestr(src.getinfo(member), src.read(member))
        size_after = wz.stat().st_size
        rewrote += 1
        if log_fn:
            mb_in = size_before / (1024 * 1024)
            mb_out = size_after / (1024 * 1024)
            elapsed = time.monotonic() - t0
            log_fn(
                f"[remote] filter ({idx}/{n_total}): {wz.stem} → "
                f"kept {kept}/{len(members)} ({mb_in:.1f}→{mb_out:.1f} MB) "
                f"in {_fmt_secs(elapsed)}\n"
            )

    # v5.2.18: raise AFTER the loop so every typo'd well is named in
    # the error message — earlier we accumulated them in empty_wells.
    if empty_wells:
        sample = ", ".join(empty_wells[:5])
        more = f" (and {len(empty_wells) - 5} more)" if len(empty_wells) > 5 else ""
        raise RuntimeError(
            f"Channel filter '{', '.join(sorted(keep_tokens))}' matched zero "
            f"images in {len(empty_wells)} well(s): {sample}{more}. "
            f"Likely a typo in the channel name(s) on the Analyze tab — "
            f"check your nuclear and fluor channel tokens against the "
            f"filenames inside the well zips."
        )

    # Only emit the summary when at least one well was actually
    # rewritten — pre-v5.2.20 the summary fired for any non-empty
    # plate, including the "all channels matched everywhere" case
    # where the line was pure noise.
    if log_fn and rewrote:
        dropped = total_in - total_kept
        log_fn(
            f"[remote] filter: summary — kept {total_kept}/{total_in} "
            f"images across {n_total} well(s), rewrote {rewrote}, "
            f"dropped {dropped}.\n"
        )
    return ZippedInput(zip_dir=zip_dir, mode=mode, well_zips=well_zips, cleanup=None)


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
