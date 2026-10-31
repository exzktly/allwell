"""Turning the user's per-well archives into what a remote run uploads.

The input must already be one ``{well}.zip`` per well — loose images and
per-well folders are refused by name, with the remedy (Datasets → Zip loose
folder), before anything is planned or sent. :func:`stage_local_zips` returns a
:class:`ZippedInput`: a directory of ``{well}.zip`` archives plus what the
caller must do afterwards. On the way it applies the channel filter and, for a
resumed run, the incremental split, both by planning from each archive's member
list before reading a byte of image data, and writing a staged copy only of the
archives that actually change.

**The user's archives are read-only here.** A filter or a split writes to a
staging directory beside the input, never over the originals; the
``ZippedInput.cleanup`` the caller runs removes it, and a failure part way
through removes it before the error propagates.
"""

from __future__ import annotations
import copy
import os
import shutil
import time
import zipfile
from dataclasses import dataclass
from services.acquisition_transport import ZippedInput, carry_staged_record
from pathlib import Path
from collections.abc import Callable, Iterable
from services.remote_config import (
    REWRITE_MIN_SAVING,
)
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
from util.zip_write import atomic_zip
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
from pipeline.input_layout import ZIP_LOOSE_FOLDER_REMEDY, non_zip_input
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

    The input must already be one ``{well}.zip`` per well: loose images and
    per-well folders are refused by name, with the same verdict and remedy as
    the pipeline and the Analyze planner (``pipeline.input_layout.
    non_zip_input``), before anything is planned or uploaded. With no filter
    and no split this is a no-op pointer at the user's archives.

    Selective staging: when ``fluor_tokens`` is restricted, the
    archives go through :func:`_plan_staging` / :func:`_materialize_staging`,
    which write a copy holding only the requested channels into a transit
    directory and upload that — cutting upload by the unused-channel fraction
    while the user's archive keeps every channel. The nuclear token is always
    kept (segmentation needs it).

    *skip_wells* are wells already finished locally
    (``services.remote_finished_wells``): their archives are left out before
    anything is planned or rewritten.

    *only_wells* (a ``--only_wells`` subset) keeps every other well's archive
    out of planning, so a channel filter or an incremental split reads and
    rewrites only the named wells and the empty-well blanks
    (*background_wells*). Without it a forced four-well run rewrote a transit
    copy of every archive on the plate before the subset was applied. It is an
    economy, not the subset itself: the caller still applies
    ``pipeline_runner.scope_staged_to_only_wells`` to whatever comes back,
    because an unfiltered plate is not planned and returns every archive.

    *on_well_count* is told, once, how many well archives the run can hold at
    most — as soon as it is known and before anything is planned or
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
    # Scanning the input volume is itself slow enough to look like a hang on a
    # spinning external disk, and it is the very first thing that happens after
    # "[remote] target:". Say so before starting.
    if log_fn is not None:
        log_fn(f"[remote] scanning input: {input_dir}\n")
    mode = LocalInputMode.ZIP
    well_zips = tuple(sorted(_well_zips_in(input_dir)))
    _refuse_non_zip_input(input_dir, well_zips)

    def _planned(well_zips, keep_tokens, zip_dir) -> ZippedInput:
        """Plan, stage and describe *well_zips*: the channel filter and the
        incremental split, whichever apply. An incremental run's archives
        always come through here — the split is what the remote side's
        no-re-attach, output wipe and merging fetch all key on.

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
                              up_to_date=tuple(up_to_date_list), log_fn=log_fn)
        staged.outside_subset_wells = outside
        return staged

    well_zips = _counted(_without_finished(well_zips, skip_wells))
    if fluor_tokens is None and not incremental:
        # No filtering — use the user's zips directly (read-only).
        return ZippedInput(zip_dir=input_dir, mode=mode, well_zips=well_zips, cleanup=None)
    # The user's input dir is read-only: a filter or split writes to a
    # staging dir, never over their archives (a mistyped channel once
    # dropped every fluor image from the originals on disk).
    return _planned(well_zips, (_required_tokens(nuclear_token, fluor_tokens)
                                if fluor_tokens is not None else None), input_dir)


def _refuse_non_zip_input(input_dir: Path, well_zips: tuple[Path, ...]) -> None:
    """Refuse loose images, stranded well folders, or no archives at all.

    ``RuntimeError`` rather than the pipeline's ``SystemExit``: this runs in
    the app, before anything is uploaded, and the runner reports a
    ``RuntimeError`` from staging as the run's error.
    """
    found = non_zip_input(input_dir, [(_canonical_well_label(p.stem), p) for p in well_zips])
    problems = found.problems()
    if not problems and not well_zips:
        problems.append("no per-well <WELL>.zip archives")
    if problems:
        raise RuntimeError(
            f"{input_dir} holds input a remote run would not read —\n  "
            + "\n  ".join(problems) + f"\n\n{ZIP_LOOSE_FOLDER_REMEDY}")


def _stage_plans(
    plans: list[_StagePlan],
    zip_dir: Path,
    *,
    mode: LocalInputMode,
    incremental: bool,
    up_to_date: tuple[str, ...],
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
            cleanup=None if staging is None else
                    (lambda d=staging: shutil.rmtree(d, ignore_errors=True)),
            incremental=incremental,
            up_to_date_wells=up_to_date)
    except Exception:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _required_tokens(
    nuclear_token: str | None, fluor_tokens: Iterable[str]
) -> set[str]:
    """Keep the nuclear channel + every requested fluor channel."""
    keep = {t.strip() for t in fluor_tokens if t and t.strip()}
    if nuclear_token:
        keep.add(nuclear_token.strip())
    return keep


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
    """``{well: path}`` for the well archives in an input directory.

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
            if path.suffix != ".zip":
                continue
            label = _canonical_well_label(path.stem)
            # A directory named like an archive is not one.
            if label and path.is_file():
                out[label] = path
    except OSError:
        return {}
    return out
