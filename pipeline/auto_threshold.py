"""Shared auto-threshold helpers used by both the pipeline and the GUI.

The pipeline (`process_microscopy.py`) and the Cell Gating tab
(`review/auto_threshold.py`) used to maintain parallel copies of
the same timepoint parser, endpoint picker, and per-image sampler.
The copies had already diverged (different sort orders, different
parser semantics) so the two paths produced different default
`thresh_frac_on` values on the same dataset.

This module is the single source of truth. It depends only on
`numpy` and `random` — no Qt, no `review.*` — so the pipeline
(which must run on headless hosts where `review` may not be
installed) can import it freely. See ARCHITECTURE.md §6.2 for the
pipeline-must-be-standalone contract.
"""

from __future__ import annotations

import contextlib
import io
import logging
import random
import zipfile
from collections.abc import Iterable
from pathlib import Path

import numpy as np
from tifffile import imread
from skimage.filters import threshold_otsu

from pipeline.threshold_optimize import round_stored_threshold
from util.image_exts import is_group_image
from util.timepoint import parse_hours as _parse_hours, sort_key as tp_sort_key


DEFAULT_CELLS_PER_IMAGE_CAP = 800


def parse_tp_hours(tp: str) -> float | None:
    """Convert a timepoint string to hours; ``None`` when unparseable.

    Delegates to :func:`util.timepoint.parse_hours`. This used to be a looser
    parser that scavenged the first digit run from anywhere in the token, which
    made it read ``"5%"`` as 5 hours and disagree with every other parser about
    ``"a1b2"``. It only fed the sort in :func:`pick_endpoint_timepoints`, so
    nothing broke — but two answers to "what hour is this token" is one too
    many, and the strict grammar is the one that produces canonical keys.
    """
    return _parse_hours(tp)


def pick_endpoint_timepoints(tps: Iterable[str]) -> list[str]:
    """Return up to three timepoints from *tps*: first, middle, last.

    Sorted chronologically using :func:`parse_tp_hours`; duplicates
    dropped so a one- or two-timepoint dataset still works.
    """
    sorted_tps = sorted(
        {str(t).strip() for t in tps if str(t).strip()},
        key=tp_sort_key,
    )
    if not sorted_tps:
        return []
    if len(sorted_tps) <= 2:
        return list(sorted_tps)
    mid = sorted_tps[len(sorted_tps) // 2]
    picked: list[str] = []
    for tp in (sorted_tps[0], mid, sorted_tps[-1]):
        if tp not in picked:
            picked.append(tp)
    return picked


def sample_cell_and_bg(
    labels: np.ndarray,
    fluor: np.ndarray,
    *,
    cap: int,
    rng: random.Random,
) -> tuple[list[float], list[float]]:
    """Return ``(cell_means, bg_pixels)`` sampled from one image.

    For every distinct cell label in ``labels`` (excluding background
    ``0``), one mean fluorescence intensity is added to ``cell_means``
    plus one random outside-cell pixel value to ``bg_pixels``. Both
    lists are capped to ``cap`` entries to keep memory bounded on
    dense fields.
    """
    if labels.shape != fluor.shape:
        return [], []
    flat_labels = labels.ravel().astype(np.int64, copy=False)
    flat_fluor = fluor.ravel().astype(np.float64, copy=False)
    nonzero = flat_labels > 0
    if not nonzero.any():
        return [], []
    bg_indices = np.flatnonzero(~nonzero)
    if bg_indices.size == 0:
        return [], []

    n_labels = int(flat_labels.max()) + 1
    sums = np.bincount(flat_labels, weights=flat_fluor, minlength=n_labels)
    counts = np.bincount(flat_labels, minlength=n_labels)
    valid = np.arange(1, n_labels)
    valid_counts = counts[1:]
    keep = valid_counts > 0
    valid = valid[keep]
    if valid.size == 0:
        return [], []
    means = (sums[valid] / counts[valid]).astype(np.float64)

    if means.size > cap:
        idx = np.array(rng.sample(range(means.size), cap), dtype=np.int64)
        means = means[idx]

    n = int(means.size)
    bg_pick = rng.choices(bg_indices.tolist(), k=n)
    bg_values = flat_fluor[np.asarray(bg_pick, dtype=np.int64)]
    return means.tolist(), bg_values.tolist()


# ── sampling a run's output archives ─────────────────────────────────────────
#
# The Otsu mode takes its samples from ``<output_dir>/*_out.zip``, pairing each
# channel's corrected ``_tophat`` image with the ``_labels`` mask of the same
# FOV-group. Output member names are the input stem plus a role suffix, so
# pairing means parsing those stems against the run's own filename schema.

_ROLE_SUFFIXES = ("_labels", "_tophat")


class _SchemaReader:
    """Reads ``(fov, timepoint, channel)`` out of an output member's stem.

    One reader for both member roles. They used to inline the same parse twice,
    and a label and its tophat that disagreed about either coordinate simply
    never paired up — silently, because the miss reads exactly like "this
    channel had no images".
    """

    def __init__(self, filename_schema: str, filename_sep: str):
        self.schema = str(filename_schema or "")
        self.sep = filename_sep or "_"
        self.fields = [f.strip().lower() for f in self.schema.split(":") if f.strip()]
        # Whether the schema names an fov slot at all — distinct from "it does,
        # but this particular stem was too short to fill it". See group_key.
        self._has_fov = "fov" in self.fields

    def parse(self, stem: str) -> dict[str, str]:
        parts = stem.split(self.sep)
        return {f: (parts[i] if i < len(parts) else "")
                for i, f in enumerate(self.fields)}

    def channel(self, stem: str) -> str:
        return (self.parse(stem).get("channel") or "").strip().lower()

    def group_key(self, stem: str) -> tuple[str, str]:
        """The ``(fov, timepoint)`` *stem* belongs to, or ``("", "")`` if unusable.

        A schema need not name every slot, and a slot it does not name is not a
        parse failure — it means the dataset has one image per remaining
        coordinate. ``well:channel:timepoint`` (files like ``A01_w2Turq_T04.tif``)
        is a real layout with no fov token to find, and two images of the same
        well/channel/timepoint cannot exist under it, because they would be the
        same filename. So an unnamed fov slot collapses to the constant ``"0"``,
        merging nothing that was ever distinct.

        An fov slot the schema *does* name but this stem does not fill is the
        opposite case: the member is malformed, and a constant there would
        silently merge genuinely different fields, so those stay rejected.

        The timepoint slot takes the constant whenever it comes out empty, named
        or not — a single-timepoint dataset writes filenames with fewer tokens
        than the schema has fields, and rejecting those dropped every member.
        """
        fields = self.parse(stem)
        fov = (fields.get("fov") or "").strip()
        # "tp" is a legacy alias a user may have typed in place of "timepoint".
        tp = (fields.get("timepoint") or fields.get("tp") or "").strip()
        if not fov and not self._has_fov:
            fov = "0"
        if not tp:
            tp = "0"
        return (fov, tp) if fov and tp else ("", "")


def _index_members(zf, reader: _SchemaReader, channels: set[str]):
    """Index one archive's members into ``(labels, tophats, unplaceable)``.

    *labels* maps ``(fov, tp)`` to a member name, *tophats* maps
    ``(channel, fov, tp)``, and *unplaceable* collects the stems that carry a
    role suffix but that the schema could not resolve — the evidence behind the
    "this schema does not describe these files" warning.
    """
    labels: dict[tuple[str, str], str] = {}
    tophats: dict[tuple[str, str, str], str] = {}
    unplaceable: list[str] = []
    for name in zf.namelist():
        # This archive is the pipeline's own ``<WELL>_out.zip`` output, not the
        # user's input, and the two questions are not the same one. Every
        # member here was written by ``_safe_imwrite`` at the top level of a
        # flat archive, so a nested member is by definition not one of them —
        # which is why the path test below is a flat refusal rather than a
        # basename test. An *input* archive is assembled by the user and may
        # legitimately carry its images in subdirectories.
        #
        # The two skips ahead of the extension test are for what a user's file
        # manager adds after the fact: opening the archive in Finder leaves a
        # ``.DS_Store`` and an ``__MACOSX/`` tree inside it. The repo's shared
        # OS-junk predicate (``datasets.core.member_names.is_os_junk_member``)
        # cannot be used here — it imports ``review``, which the pipeline
        # may not — so this is the local spelling of the same two rules.
        #
        # The extension test was spelled out here as ``tif``/``tiff`` only,
        # while the pipeline also writes ``.png`` — and because the skip happens
        # *before* the role and schema checks below, a PNG archive left
        # ``unplaceable`` empty too, so the "none of its members could be
        # placed" warning could not fire and the whole plate reported one line
        # per channel: "only 0 sample(s)".
        if "/" in name or name.startswith("."):
            continue
        if not is_group_image(name):
            continue
        base, _, _ext = name.rpartition(".")
        suffix = next((s for s in _ROLE_SUFFIXES if base.endswith(s)), None)
        if suffix is None:
            continue
        stem = base[: -len(suffix)]
        key = reader.group_key(stem)
        if key == ("", ""):
            unplaceable.append(stem)
            continue
        if suffix == "_labels":
            labels[key] = name
        else:
            ch = reader.channel(stem)
            if ch in channels:
                tophats[(ch, *key)] = name
    return labels, tophats, unplaceable


def _index_input_frames(zf, reader: _SchemaReader, channels: set[str]):
    """Index an *input* archive's raw frames for *channels*: ``(ch, fov, tp)`` → name.

    The counterpart of :func:`_index_members` for a channel whose corrected
    image the run deliberately did not archive — the phase channel under
    ``--phase_output reference`` (``pipeline.output_images``), whose
    "corrected" frame *is* its raw input frame. Unlike an output archive, an
    input archive is assembled by the user, so members may sit in
    subdirectories and are judged by basename; the OS-junk members a file
    manager leaves behind (``__MACOSX/``, dotfiles) are skipped by the same two
    rules the output indexer spells out, and a member carrying a pipeline role
    suffix is not a raw frame.
    """
    frames: dict[tuple[str, str, str], str] = {}
    for name in zf.namelist():
        if name.endswith("/") or name.startswith("__MACOSX/"):
            continue
        base_name = name.rsplit("/", 1)[-1]
        if not base_name or base_name.startswith(".") or not is_group_image(base_name):
            continue
        base = base_name.rpartition(".")[0]
        if any(base.endswith(s) for s in _ROLE_SUFFIXES):
            continue
        key = reader.group_key(base)
        ch = reader.channel(base)
        if key != ("", "") and ch in channels:
            frames.setdefault((ch, *key), name)
    return frames


def _sample_one_well(zf, labels, tophats, *, channels, picked_tps, per_channel,
                     attempted, decode_failed, cap, rng, log,
                     input_zf=None, input_frames=None) -> None:
    """Accumulate per-cell + background samples for one archive, in place.

    A ``(ch, fov, tp)`` with no archived ``_tophat`` is read from *input_zf*
    when *input_frames* names one there — see :func:`_index_input_frames` for
    which channels may be. An archived member always wins, so a well an
    earlier copy-mode run wrote keeps being read from its own archive.
    """
    input_frames = input_frames or {}
    for (fov, tp), label_name in labels.items():
        if tp not in picked_tps:
            continue
        for ch in channels:
            fluor_name = tophats.get((ch, fov, tp))
            fluor_zf = zf
            if fluor_name is None and input_zf is not None:
                fluor_name = input_frames.get((ch, fov, tp))
                fluor_zf = input_zf
            if fluor_name is None:
                continue
            attempted[ch] += 1
            try:
                labels_arr = imread(io.BytesIO(zf.read(label_name)))
                fluor_arr = imread(io.BytesIO(fluor_zf.read(fluor_name)))
            except Exception as exc:
                decode_failed[ch] += 1
                log.debug("Auto-threshold: skipping %s / %s: %s",
                          label_name, fluor_name, exc)
                continue
            if labels_arr.ndim == 3:
                labels_arr = labels_arr[..., 0]
            if fluor_arr.ndim == 3:
                fluor_arr = fluor_arr[..., 0]
            means, bg_values = sample_cell_and_bg(
                labels_arr, fluor_arr, cap=cap, rng=rng)
            if not means:
                continue
            per_channel[ch].extend(means)
            per_channel[ch].extend(bg_values)


def _otsu_per_channel(channels, per_channel, attempted, decode_failed, log
                      ) -> dict[str, float]:
    """Reduce the pooled samples to one threshold per channel."""
    thresholds: dict[str, float] = {}
    for ch in channels:
        # A biased threshold reports itself: if a large fraction of this
        # channel's tophat/label pairs failed to decode, the Otsu estimate is
        # computed from a non-representative subset (audit E9).
        n_try = attempted.get(ch, 0)
        n_bad = decode_failed.get(ch, 0)
        if n_try and n_bad / n_try >= 0.25:
            log.warning(
                "Auto-threshold: %s — %d/%d image pair(s) failed to decode "
                "(%.0f%%); the threshold is estimated from the remaining "
                "subset and may be biased. Check for corrupt *_out.zip members.",
                ch.upper(), n_bad, n_try, 100.0 * n_bad / n_try,
            )
        values = per_channel.get(ch) or []
        if len(values) < 2:
            log.info("Auto-threshold: skipping %s — only %d sample(s)",
                     ch.upper(), len(values))
            continue
        arr = np.asarray(values, dtype=np.float64)
        if arr.max() <= arr.min():
            log.info("Auto-threshold: skipping %s — constant distribution "
                     "(min == max == %.3g)", ch.upper(), float(arr.min()))
            continue
        try:
            raw = float(threshold_otsu(arr))
        except Exception as exc:
            log.warning("Auto-threshold: Otsu failed for %s: %s", ch.upper(), exc)
            continue
        if not np.isfinite(raw):
            log.warning("Auto-threshold: skipping %s — Otsu returned a "
                        "non-finite threshold (%r).", ch.upper(), raw)
            continue
        # Rounded for storage, but never rounded away: a normalised 0-1 channel
        # whose true cut is 0.4048 used to be stored as 0.0, which is both a gate
        # that passes every cell and the value this function uses to mean "I
        # could not measure this channel" (it omits the key). See
        # ``round_stored_threshold``.
        thr = round_stored_threshold(raw)
        thresholds[ch] = thr
        log.info("Auto-threshold: %s → %.4g (n=%d)", ch.upper(), thr, int(arr.size))
    return thresholds


@contextlib.contextmanager
def _open_input_archive(input_dir, out_zip: Path, referenced: set[str], log):
    """Yield the ``<WELL>.zip`` input archive beside *out_zip*'s well, or ``None``.

    ``None`` when no channel is referenced, no *input_dir* was given, or the
    archive is absent or unreadable — the last two logged, because a phase
    channel that silently contributed nothing would read as a channel with no
    signal.
    """
    if not referenced or input_dir is None:
        yield None
        return
    well = out_zip.name[: -len("_out.zip")] if out_zip.name.endswith("_out.zip") else out_zip.stem
    in_zip = Path(input_dir) / f"{well}.zip"
    if not in_zip.is_file():
        log.info("Auto-threshold: %s — no input archive %s, so %s (read from the "
                 "input, not archived) contributes no samples from this well.",
                 out_zip.name, in_zip.name, ", ".join(sorted(c.upper() for c in referenced)))
        yield None
        return
    try:
        in_zf = zipfile.ZipFile(in_zip, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        log.warning("Auto-threshold: cannot open input archive %s: %s", in_zip.name, exc)
        yield None
        return
    with in_zf:
        yield in_zf


def no_threshold_reason(*, has_control_wells: bool) -> str:
    """Why the end-of-run pass produced nothing, in the mode it actually ran.

    The two modes fail for unrelated reasons and only one of them reads an
    output archive. Naming the Otsu reason under the control method sends the
    user to inspect archives that nothing opened — the same substitution this
    codebase treats as a defect everywhere else, a failure reported as a
    different failure. The control method's own failures are per channel and
    already in the log; the likeliest by far is that the two wells do not
    separate, which its predecessor could not report at all because it always
    returned a number.
    """
    if has_control_wells:
        return ("None of the per-channel comparisons between the two control "
                "wells succeeded — see the per-channel lines above, which name "
                "the reason for each.")
    return ("The per-well output archives yielded no usable image pairs — see "
            "the per-well lines above for why.")


def estimate_thresholds_from_outputs(
    output_dir: Path,
    *,
    fluor_channels: list[str],
    filename_schema: str,
    filename_sep: str,
    log: logging.Logger,
    cells_per_image_cap: int = DEFAULT_CELLS_PER_IMAGE_CAP,
    rng_seed: int | None = None,
    input_dir: Path | None = None,
    referenced_channels: Iterable[str] = (),
) -> dict[str, float]:
    """Otsu per-channel thresholds sampled from a run's ``*_out.zip`` archives.

    Returns ``{channel: threshold}``, omitting any channel it could not measure.
    Each channel's corrected (``_tophat``) image is paired with the segmentation
    mask (``_labels``) of the same FOV-group, so the estimate is over per-cell
    means against matched background rather than over raw pixels.

    Pairing means reading ``(fov, timepoint)`` out of member names using the
    run's own *filename_schema* — see :meth:`_SchemaReader.group_key` for what
    happens when that schema does not name every slot.

    *referenced_channels* are channels whose corrected frame the run chose not
    to archive because it is identical to the input frame (the phase channel
    under ``--phase_output reference``). Their frames are read from
    ``<input_dir>/<WELL>.zip`` instead, so referencing the phase image does not
    cost that channel its threshold. A well whose input archive is not there
    says so and contributes no samples for those channels.
    """
    channels = [str(c).strip().lower() for c in fluor_channels if str(c).strip()]
    if not channels:
        return {}
    output_dir = Path(output_dir)
    if not output_dir.exists():
        log.warning("Auto-threshold: output dir not found (%r)", str(output_dir))
        return {}

    reader = _SchemaReader(filename_schema, filename_sep)
    referenced = {str(c).strip().lower() for c in referenced_channels
                  if str(c).strip()} & set(channels)
    rng = random.Random(rng_seed)
    per_channel: dict[str, list[float]] = {c: [] for c in channels}
    # Decode-failure accounting per channel (audit E9): a failure is DEBUG-only,
    # so a channel whose tophat images are mostly corrupt would otherwise
    # silently estimate from a small, biased subset.
    attempted: dict[str, int] = {c: 0 for c in channels}
    decode_failed: dict[str, int] = {c: 0 for c in channels}

    well_zips = sorted(p for p in output_dir.glob("*_out.zip")
                       if not p.name.startswith("."))
    if not well_zips:
        log.info("Auto-threshold: no processed *_out.zip wells in %s", output_dir)
        return {}

    log.info("Auto-threshold: scanning %d well(s) for channels: %s",
             len(well_zips), ", ".join(channels))

    for w_idx, zpath in enumerate(well_zips, start=1):
        try:
            zf = zipfile.ZipFile(zpath, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            log.warning("Auto-threshold: cannot open %s: %s", zpath.name, exc)
            continue
        with zf:
            labels, tophats, unplaceable = _index_members(zf, reader, set(channels))
            if not labels:
                # An archive holding role-suffixed members that the schema
                # cannot place is a configuration mismatch, not an empty well,
                # and the two used to be indistinguishable: the per-channel
                # "only 0 sample(s)" lines read as "this channel had no data"
                # and named neither the schema nor a filename.
                if unplaceable:
                    log.warning(
                        "Auto-threshold: %s — none of its %d image member(s) could "
                        "be placed by the run's filename schema %r (separator %r); "
                        "no thresholds can be estimated from this well. "
                        "Example member stem: %r",
                        zpath.name, len(unplaceable), reader.schema, reader.sep,
                        unplaceable[0],
                    )
                else:
                    # No mask members and nothing the schema rejected either, so
                    # the archive simply holds no ``_labels`` image. Named here
                    # rather than left to the per-channel "only 0 sample(s)"
                    # lines, which describe the channel and not the well.
                    log.warning(
                        "Auto-threshold: %s holds %d member(s) but no "
                        "'_labels' segmentation image, so no cell can be "
                        "sampled from it.", zpath.name, len(zf.namelist()),
                    )
                continue
            picked_tps = set(pick_endpoint_timepoints(
                {tp for (_fov, tp) in labels}))
            if not picked_tps:
                continue
            log.info("Auto-threshold: %s → timepoints %s",
                     zpath.name, ", ".join(sorted(picked_tps)))
            with _open_input_archive(input_dir, zpath, referenced, log) as in_zf:
                _sample_one_well(
                    zf, labels, tophats, channels=channels, picked_tps=picked_tps,
                    per_channel=per_channel, attempted=attempted,
                    decode_failed=decode_failed, cap=cells_per_image_cap,
                    rng=rng, log=log, input_zf=in_zf,
                    input_frames=(_index_input_frames(in_zf, reader, referenced)
                                  if in_zf is not None else None))
        log.info("Auto-threshold: completed well %d/%d (%s)",
                 w_idx, len(well_zips), zpath.name)

    return _otsu_per_channel(channels, per_channel, attempted, decode_failed, log)
