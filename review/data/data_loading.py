"""Pure data helpers (CSV loading, timepoint parsing, aggregation, beeswarm).

All row-level operations work on ``pandas.DataFrame``. The canonical row
container in the app is a DataFrame; ``ReviewApp._get_rows(label)`` returns
one and the per-well cache stores it.
"""

from __future__ import annotations

import logging
import math
import os
from contextlib import nullcontext
import re
from pathlib import Path
from collections.abc import Iterator
from typing import Any

import numpy as np
import pandas as pd

from review.data.cache_projectors import iter_cache_projectors
from review.domain.fov_integrity import check_fov_labels
from review.domain.gating_state import active_fluor_gates
from review.domain.ratio_models import RatioMetric, is_ratio_key
from domain.text_columns import (
    TEXT_COLUMNS,
    fov_text,
    timepoint_text,
)
from domain.tp_match import tp_close_mask


_logger = logging.getLogger("review")

# One-shot dedupe for the "missing fluor column" warning so a 96-well
# dataset with a schema mismatch logs once per channel, not 96 times.
_missing_col_warned: set[str] = set()


# ── CSV loading and channel detection ────────────────────────────────────────

# Columns kept as strings (not coerced to float). Defined once in
# `domain.text_columns`, beside the helpers that read them back out, so the
# loader that types them and the guard that watches how they are read cannot
# disagree about which columns are involved.
_STRING_COLS = TEXT_COLUMNS

# Per-channel measurement column suffixes — the prefix is a channel token,
# which the rest of the app handles in lower-case (normalize_channel_tokens).
# Lower-casing these at read time keeps the CSV-on-disk schema (where casing
# depends on whatever wrote the column — pipeline, smFISH worker, hand edit)
# in sync with the lower-case convention every downstream lookup uses.
_METRIC_SUFFIXES = (
    "_mean_intensity", "_total_intensity", "_max_intensity",
    "_min_intensity", "_std_intensity", "_smfish_count",
)


from domain.image_identity import (  # after _METRIC_SUFFIXES decl
    parse_timepoint_hours,  # re-export: single source of truth in image_identity
    normalize_fov_series as _normalize_fov_series,
)
from core.safe import swallowed


def normalize_fov_series(fov: pd.Series) -> pd.Series:
    """Re-exported from :mod:`domain.image_identity` — the
    single source of truth for FOV normalisation. Empty/``"-1"`` → ``"1"``
    matches the single-FOV-extractor convention so plots, gating, and the
    smFISH apply-global writer agree on which rows belong to which image."""
    return _normalize_fov_series(fov)


def _string_column_dtypes(path: Path) -> dict:
    """``{column: str}`` for the text columns, keyed on their on-disk names.

    A one-row-free header read, so the main read below can be told which
    columns are text and left to type the rest itself. The names are the
    *unrenamed* ones because the rename happens after the read.
    """
    header = pd.read_csv(path, nrows=0)
    return {c: str for c in header.columns
            if str(c).strip().lower() in _STRING_COLS}


#: A text column is worth storing as codes only while it repeats. Above this
#: share of distinct values the codes array stops paying for itself and starts
#: costing an extra int32 per row on top of the strings.
_CATEGORY_MAX_UNIQUE_SHARE = 0.5


def _categorize_text_columns(df: pd.DataFrame) -> None:
    """Store the repeating text columns as categories, in place.

    These six columns are the bulk of a loaded plate and almost all of it is
    repetition. Profiling a 537,393-row plate: 32 numeric columns came to
    131 MiB and the six text ones to 195 MiB — of which ``experiment`` and
    ``channel`` held **one distinct value each** and cost 30 MiB apiece, and
    the most varied, ``filename``, held 1,248. As categories the frame goes
    from 327 MiB to 135 MiB, and that is steady-state memory in the row cache
    rather than a transient, held for every well of every open dataset.

    Three things make this safe, each of which was measured rather than
    reasoned about, and each of which fails loudly or silently if dropped:

    * **``""`` is always a category.** ``fillna("")`` validates its argument
      against the categories whether or not any value needs filling, so a
      column without it raises ``TypeError: Cannot setitem on a Categorical
      with a new category`` — ``fov_integrity.check_fov_labels`` does exactly
      that on every load.
    * **Grouping is unaffected**, because the categories are the observed
      values. A category list wider than the data would make
      ``groupby`` emit a group per *unused* category — 16 groups where there
      are 2, 14 of them NaN — which would quietly fabricate per-field
      statistics. Both real grouping sites were run both ways and agree.
    * **Pooling falls back to ``object``**, because ``pd.concat`` of columns
      whose categories differ cannot keep the type. That is the status quo,
      not a regression, and it measures *cheaper* than before: pooling 26
      wells added 14 MiB from categories against 29 MiB from strings, since
      the category objects are shared rather than rebuilt.

    A column that does not repeat is left alone — see
    ``_CATEGORY_MAX_UNIQUE_SHARE``.
    """
    n = len(df)
    if not n:
        return
    for col in df.columns:
        if str(col).strip().lower() not in _STRING_COLS:
            continue
        values = df[col]
        if values.dtype != object:
            continue
        if values.nunique(dropna=False) > n * _CATEGORY_MAX_UNIQUE_SHARE:
            continue
        categories = sorted(set(values.unique()) | {""})
        df[col] = pd.Categorical(values, categories=categories)


def load_well_csv(path: Path) -> pd.DataFrame:
    """Load a per-well CSV into a DataFrame.

    String columns (``_STRING_COLS``) stay as object dtype; any other column
    is coerced via ``pd.to_numeric(errors="coerce")``. Non-canonical casings
    of the string columns ("FOV" → "fov") are renamed, as are per-channel
    measurement columns whose channel-prefix uses upper-case
    ("W3Cy5_smfish_count" → "w3cy5_smfish_count"). The ``Included`` column
    is added with default value 1 if missing. Empty/"-1" FOVs are normalised
    to "1".

    **Only the text columns are read as text.** Reading the whole file with
    ``dtype=str`` makes pandas allocate one consolidated object block of every
    column at once — ``(n_columns, n_rows)`` — and fill it with a Python string
    per cell, including for the columns that are numbers and are about to be
    converted back. On a 537,393-row × 37-column file that is a 152 MiB pointer
    array plus roughly 16.6 million short strings, and it is where a plate that
    size died with ``Unable to allocate 152 MiB for an array with shape
    (37, 537393) and dtype object``. Measured on a file of exactly that shape,
    each read in its own process: 1729 MiB peak before, 867 MiB after. The
    separate processes are the point — ``ru_maxrss`` is a high-water mark that
    never falls, so measuring both reads in one process reports the first
    strategy's peak as the second one's.

    Chunking the old read does **not** help — the coerced chunks stay alive
    until the concat — which is why this types the read instead.

    ``na_values=[""]`` with ``keep_default_na=False`` is what keeps the result
    byte-identical to the old path: a blank becomes NaN in every column (then
    ``""`` again in the text ones), while a cell holding the literal text
    ``NA`` / ``NaN`` / ``null`` stays that text in a string column and still
    coerces to NaN in a numeric one, exactly as before.
    """
    df = pd.read_csv(path, dtype=_string_column_dtypes(path),
                     keep_default_na=False, na_values=[""])
    if df.empty:
        return df
    for col in df.columns:
        if str(col).strip().lower() in _STRING_COLS:
            df[col] = df[col].fillna("")

    def _should_lower(col: str) -> bool:
        lc = str(col).strip().lower()
        if lc in _STRING_COLS:
            return True
        return any(lc.endswith(suf) for suf in _METRIC_SUFFIXES)

    rename = {
        col: str(col).strip().lower()
        for col in df.columns
        if _should_lower(col) and col != str(col).strip().lower()
    }
    if rename:
        df = df.rename(columns=rename)

    if "fov" in df.columns:
        df["fov"] = normalize_fov_series(df["fov"])

    if "Included" not in df.columns:
        df["Included"] = 1

    for col in df.columns:
        if str(col).strip().lower() in _STRING_COLS:
            continue
        # A column the reader already typed needs nothing: re-running
        # ``to_numeric`` over a float64 column would rebuild it for no gain.
        # ``object`` is what is left when a numeric column carries a stray
        # non-numeric cell, and that is exactly the case this coerces.
        if df[col].dtype == object:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    _categorize_text_columns(df)

    # Backfill circularity for datasets processed before it became always-on,
    # when its inputs are present (perimeter + an area column). 4π·area/perimeter²
    # (1.0 = perfect circle). Lets the Segmentation QC circularity gate + the
    # plot dropdowns work without a full reprocess; datasets processed with the
    # current pipeline already carry ``shape_circularity`` directly.
    if "shape_circularity" not in df.columns and "shape_perimeter" in df.columns:
        area_col = ("shape_area" if "shape_area" in df.columns
                    else "area_px" if "area_px" in df.columns else None)
        if area_col is not None:
            from math import pi
            per = pd.to_numeric(df["shape_perimeter"], errors="coerce")
            area = pd.to_numeric(df[area_col], errors="coerce")
            # Mask non-positive perimeters BEFORE dividing (audit S8) so
            # per == 0 yields NaN directly instead of inf/NaN + a RuntimeWarning.
            safe_per = per.where(per > 0)
            df["shape_circularity"] = (4.0 * pi * area) / (safe_per * safe_per)

    return df


def detect_fluor_channels(df: pd.DataFrame) -> list[str]:
    """Return sorted channel prefixes that have a ``*_mean_intensity`` column."""
    if df is None or df.empty:
        return []
    suffix = "_mean_intensity"
    return sorted({c[:-len(suffix)] for c in df.columns
                   if c.endswith(suffix) and len(c) > len(suffix)})


def detect_smfish_channels(df: pd.DataFrame) -> list[str]:
    """Return sorted channel prefixes that have a ``*_smfish_count`` column."""
    if df is None or df.empty:
        return []
    suffix = "_smfish_count"
    return sorted({c[:-len(suffix)] for c in df.columns
                   if c.endswith(suffix) and len(c) > len(suffix)})


def detect_shape_features(df: pd.DataFrame) -> list[str]:
    """Return the per-cell ``shape_*`` columns present in *df*, in catalog order.

    Channel-independent morphology columns emitted by the pipeline's
    ``compute_shape_features`` pass. Empty on datasets analyzed before the
    shape-feature work (backwards-compatible — the Review "Shape Features"
    dropdown section simply does not appear). Order follows the
    :mod:`shape_features_core` catalog so the dropdown is deterministic.
    """
    if df is None or df.empty:
        return []
    import pipeline.shape_features as sfc
    present = set(df.columns)
    return [c for c in sfc.ALL_COLUMNS if c in present]


def detect_nuclear_channel_token(df: pd.DataFrame) -> str:
    """Return the nuclear/segmentation channel token from CSV ``channel`` column."""
    if df is None or df.empty or "channel" not in df.columns:
        return ""
    val = df["channel"].iloc[0]
    return str(val or "").strip().lower()


def normalize_channel_tokens(tokens: list[str]) -> list[str]:
    """Stable lower-case de-dupe for channel tokens."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        cleaned = str(tok or "").strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def merge_fluor_channels(
    pipeline_fluor: list[str],
    detected_fluor: list[str],
    seg_channel_token: str = "",
) -> list[str]:
    """Merge pipeline + detected channel lists with stable order, always including seg token."""
    merged = normalize_channel_tokens(list(pipeline_fluor) + list(detected_fluor))
    seg_tok = str(seg_channel_token or "").strip().lower()
    if seg_tok and seg_tok not in merged:
        merged.append(seg_tok)
    return merged


def detect_review_image_channels(df: pd.DataFrame, fluor_channels: list[str], seg_channel_token: str = "") -> list[str]:
    """Return channel prefixes suitable for Review Image."""
    chans: list[str] = []
    seen: set[str] = set()
    for ch in fluor_channels:
        tok = str(ch or "").strip().lower()
        if tok and tok not in seen:
            seen.add(tok)
            chans.append(tok)
    seg_tok = str(seg_channel_token or "").strip().lower()
    if seg_tok and seg_tok not in seen:
        seen.add(seg_tok)
        chans.append(seg_tok)
    return chans


# ── DataFrame helpers ────────────────────────────────────────────────────────

#: Entry points that have already announced a missing ``area_px``, so the
#: warning below is one line per load rather than one per redraw. Cleared by
#: ``reset_missing_area_warnings`` when a dataset is opened: a process-global
#: that is never reset says nothing at all about the *second* dataset a session
#: opens, which is the one the user is more likely to be puzzled by.
_missing_area_warned: set[str] = set()


def reset_missing_area_warnings() -> None:
    """Let the next dataset announce its own missing ``area_px`` column."""
    _missing_area_warned.clear()


def _warn_no_area_column(where: str) -> None:
    """Say out loud that a frame has no ``area_px``, once.

    Both aggregation entry points return an empty result when the column is
    absent, which is correct — cell area is the gate every downstream statistic
    is defined against — but they returned it silently. Every plot in the app
    then drew nothing at all, with no status line, no log line and no empty
    state naming a cause, on a dataset that had loaded without complaint. A
    CSV written by an older pipeline, or one hand-edited down to a few columns,
    lands exactly here.
    """
    if where in _missing_area_warned:
        return
    _missing_area_warned.add(where)
    _logger.warning(
        "%s: the loaded CSVs have no 'area_px' column, so no cells can be "
        "gated by area and every aggregation returns empty — the plots will "
        "draw nothing. This column is written by the segmentation step; a CSV "
        "from an older pipeline run, or one that has been edited down, will "
        "not have it.", where,
    )


#: The verdict columns composed with ``Included``, in the order they are
#: ANDed. Written once so a surface that needs the composition *minus one
#: layer* subtracts from this rather than restating it -- the focus panel
#: counts over the cells that would otherwise be analysed, which is neither
#: every row in the frame nor the rows its own cut has already judged.
#:
#: ``Included`` is not in it: it is the only one whose absence means all-pass
#: for a different reason (no curation has happened) and whose blanks mean
#: dropped rather than kept.
VERDICT_LAYERS = ("QC_Pass", "Crop_Pass", "Gate_Pass", "Field_Pass", "Focus_Pass",
                  "Excluded_Field_Pass")


def df_included_mask(df: pd.DataFrame, *, include_gate: bool = True,
                     except_layer: str | None = None) -> pd.Series:
    """Rows that count toward analysis: ``Included == 1`` composed with the QC,
    crop, scatter-gate and field-quality layers.

    Manual curation / cell-gating own the ``Included`` column; the QC layer owns
    a separate ``QC_Pass`` column (1 = passes debris gating); the crop layer owns
    a ``Crop_Pass`` column (1 = centroid inside the crop region); the scatter-gate
    inclusion mode owns ``Gate_Pass`` (1 = inside the drawn scatter gates). The
    effective inclusion is the AND of the four — ``manual-keep AND passes-QC AND
    inside-crop AND inside-gate`` — so each layer re-gating never clobbers the
    others. Two more join them, both off by default and both about image
    quality rather than about the cells:

    * ``Field_Pass`` (1 = the run did not flag this row's field) is written
      only when the user has turned that exclusion on and only for wells whose
      FOV labels can be trusted — see :mod:`review.data.field_quality_cache`,
      which explains why an untrusted well is refused rather than joined onto.
    * ``Focus_Pass`` (1 = this cell's own outline was not soft) is an absolute
      cut on the per-cell focus column — see :mod:`review.data.focus_gate`.
      It is the one layer here that can remove *part* of a field, which is
      what a focus gradient or a coverslip tilt actually looks like.
    * ``Excluded_Field_Pass`` (1 = the user did not exclude this row's field
      by hand) is written only for wells holding such an exclusion, from QC
      triage — see :mod:`review.data.field_exclusions`. It refuses the same
      untrusted wells ``Field_Pass`` does, for the same reason.

    Neither subsumes the other: a field whose median is fine can still have a
    soft third, and a uniformly soft field is invisible to any per-cell cut
    relative to its own frame, which is why the per-cell one is absolute.

    Every verdict column — ``QC_Pass`` / ``Crop_Pass`` / ``Gate_Pass`` /
    ``Field_Pass`` / ``Focus_Pass`` / ``Excluded_Field_Pass`` — is optional:
    when absent (no such layer / older data / the exclusion switched off /
    nothing excluded in that well) each is treated as all-pass.

    ``include_gate=False`` drops the ``Gate_Pass`` layer. The per-cell scatter —
    where the gates are drawn — passes it by default, so it keeps showing the
    full cloud with the gates over it rather than filtering itself by its own
    gates; its "Apply gates" toggle opts back in.

    ``except_layer`` drops one named verdict column, for a surface that is
    **about** that layer and must not filter its own evidence by it: the focus
    panel counting how many cells a cut would remove has to count over the
    cells that would otherwise be in the analysis — neither over the debris the
    QC gate already dropped, nor over the ones its own cut is about to.
    """
    if "Included" not in df.columns:
        mask = pd.Series(True, index=df.index)
    else:
        incl = pd.to_numeric(df["Included"], errors="coerce").fillna(0)
        mask = incl.eq(1)
    for column in VERDICT_LAYERS:
        if column == except_layer:
            continue
        if column == "Gate_Pass" and not include_gate:
            continue
        if column not in df.columns:
            continue
        # ``fillna(1)`` throughout: an unreadable verdict keeps the row. A cell
        # nobody could measure and a cell measured and found wanting are
        # different facts, and only the second is a reason to drop anything.
        verdict = pd.to_numeric(df[column], errors="coerce").fillna(1)
        mask = mask & verdict.eq(1)
    return mask


def read_well_frame(path: Path) -> pd.DataFrame:
    """One well's parsed measurement frame, from the Parquet cache or the CSV.

    **The one door onto a well's rows**, so the cache cannot be consulted on one
    path and skipped on another. On a miss the CSV is parsed by
    :func:`load_well_csv` exactly as before and the result is cached for next
    time; on a hit the parse is skipped, which measured 51 ms against 3,068 ms on
    a 120,000-row well.

    The frame cached here is the CSV's own parse, **before** any verdict column
    is projected onto it — see :mod:`review.data.parquet_cache`. That is not
    incidental to where this function sits: it is why the write belongs here,
    one call above :func:`load_and_project`'s projector loop, rather than
    anywhere the frame has already been through it.

    The fingerprint is taken **before** the parse and handed to the write, which
    declines if the file has moved on since. Stamping the file's state after a
    three-second read would record the new file's identity against the old
    file's rows whenever something rewrote the CSV in between, and that cache is
    then wrong on every read afterwards with nothing to notice it by.
    """
    from review.data.parquet_cache import (
        read_cached, source_fingerprint, write_cache,
    )

    cached = read_cached(path)
    if cached is not None:
        return cached
    before = source_fingerprint(path)
    frame = load_well_csv(path)
    write_cache(path, frame, before)
    return frame


class WellDoesNotFitInMemory(MemoryError):
    """A well's rows could not be read for lack of memory, even with the row cache emptied.

    A ``MemoryError`` subclass, so every handler that already catches one still
    does; what changes is the sentence. The bare error names a numpy allocation —
    "Unable to allocate 8.43 MiB for an array with shape (1104480,)" — which says
    nothing about which well, how large it is, or what was already held, and an
    8 MiB allocation failing reads as a bug rather than as a machine out of
    memory. This one names all three.
    """


def _mib(n: float) -> str:
    return f"{n / 2**20:,.0f} MiB"


def _read_making_room(dataset, label: str, path: Path) -> pd.DataFrame:
    """:func:`read_well_frame`, retried once with the row cache emptied on a ``MemoryError``.

    The held frames are the one large thing this layer can give back. Dropping
    them costs re-reading those wells later (0.22 s each from the Parquet cache);
    not dropping them costs the well being asked for. A second failure is raised
    as :class:`WellDoesNotFitInMemory`, naming the well, its size on disk, what
    the cache held when the first attempt failed, and its budget.
    """
    try:
        return read_well_frame(path)
    except MemoryError as first:
        cache = dataset.cache
        held = cache.describe() if hasattr(cache, "describe") else f"{len(cache)} well(s) held"
        _logger.warning("Out of memory reading well %s (%s); emptying the row cache and trying once more",
                        label, held)
        release = getattr(cache, "release", None)
        if release is not None:
            release()
        else:
            cache.clear()
        try:
            return read_well_frame(path)
        except MemoryError as second:
            try:
                on_disk = _mib(os.path.getsize(path))
            except OSError:
                on_disk = "an unknown size"
            raise WellDoesNotFitInMemory(
                f"Well {label} could not be read: the computer ran out of memory. Its CSV is {on_disk} on disk; "
                f"when the read began the {held}. Emptying that cache and retrying did not help, so this well "
                f"alone needs more memory than is free. Close other programs, or open the plate on a machine "
                f"with more memory. (The failing allocation: {first})"
            ) from second


def get_rows(ctx, label: str) -> pd.DataFrame:
    """Return the measurement frame for well *label*, loading + projecting lazily.

    The dataset's row cache holds one ``DataFrame`` per loaded well. On first
    access this reads the well CSV, then projects the composable ``QC_Pass`` /
    ``Crop_Pass`` verdict columns onto the fresh frame so the inclusion
    chokepoint (:func:`df_included_mask`) sees a consistent row.

    A8 residuals-ledger R1: the ctx-native row-fetch data service. Cache + paths
    come from ``ctx.state.dataset``. ``ReviewApp._get_rows`` delegates here,
    so every caller — this module's ``get_all_timepoints``, ``stats_compute``,
    and the feature ``collect`` modules — can fetch rows from ``ctx`` without
    naming the god-object.

    The load itself is :func:`load_and_project`, which the dataset loader's bulk
    pass also calls: one path in, so a lazily reloaded well carries exactly the
    layers a freshly opened dataset's well does.
    """
    # One lookup, not ``in`` then ``[]``: the cache is bounded, and a well held at
    # the first may be evicted by another thread's read before the second.
    try:
        frame = ctx.state.dataset.cache[label]
    except KeyError:
        frame = None
    # Loaded outside the ``except``: a failure there would otherwise be reported
    # as raised "during handling of" a KeyError, which reads as a second fault.
    if frame is None:
        frame = load_and_project(ctx, label)
    return frame


def load_and_project(ctx, label: str, path: Path | None = None) -> pd.DataFrame:
    """Read well *label* from disk into the row cache, projected and recorded.

    **The single way a frame enters the row cache.** Both the lazy path
    (:func:`get_rows`, on a cache miss) and the dataset loader's eager bulk pass
    (through ``WellRepository.load_well``) come through here, so neither can
    acquire a step the other lacks. They diverged once: the loader applied manual
    curation as well as QC and crop, while a lazily reloaded well got only the
    two registered projectors — so a well the cache dropped came back with the
    user's per-cell exclusions silently gone.

    *path* registers the well's CSV before reading it, which is what the loader's
    first pass over a new dataset needs; omitted, the path already in
    ``dataset.well_paths`` is used.

    The verdict columns are applied via the **cache-projector registry**
    (``data/cache_projectors.py``) rather than a function-local ``features/``
    import — the data layer stays feature-free (the inversion enforced by
    ``tests/test_data_layer_no_feature_imports.py``). The defaults (curation,
    then QC, then crop) are registered at app construction by
    ``features/registry.build_feature_registry``, which is the one module
    allowed to name a feature.
    """
    dataset = ctx.state.dataset
    if path is not None:
        dataset.well_paths[label] = path
    frame = _read_making_room(dataset, label, dataset.well_paths[label])
    pinned = getattr(dataset.cache, "pinned", None)
    # Pinned while the projectors run: they find the frame by label, and one that
    # found it evicted meanwhile (a read on the gating worker's thread) would
    # skip its verdict and leave the well looking fully included.
    with (pinned(label) if pinned is not None else nullcontext()):
        dataset.cache[label] = frame
        for _project in iter_cache_projectors():
            # **Guarded per projector, matching ``qc.controller.recompute_qc_all``.**
            # The same registry list is walked from two places, and until now the
            # two had different failure contracts: a raise there lost one verdict
            # column, a raise here lost the whole WELL, because this is the call
            # that decides whether a frame enters the cache at all. One layer must
            # never take down the others, and losing a well is the larger of the
            # two failures, so the unguarded one was the wrong side of the
            # asymmetry. Each projector already reports its own per-well failures
            # through ``project_verdict_column``; this catches a projector that
            # raises outside that walk.
            try:
                _project(ctx, label)
            except Exception:
                _logger.exception(
                    "cache projector %r failed for well %r", _project, label)
    # The loader used to have to record this itself, having built the frame
    # outside this function; the verdict now travels with the load.
    record_fov_verdict(ctx, label, frame)
    # The projectors added columns in place; the budget should count them. The
    # local *frame* is what is returned: a frame larger than the budget is held
    # alone, and another thread's read may have evicted it since the unpin.
    remeasure = getattr(dataset.cache, "remeasure", None)
    if remeasure is not None:
        remeasure(label)
    return frame


def held_wells_note(ctx) -> str:
    """``""`` when every well of the dataset is in the row cache, else a clause
    saying how many are — for a count taken over the held frames.

    The live readouts (the QC row's "flagged", the crop's status line, Cell
    Gating's "Included for analysis") are recomputed on every edit, so they count
    the frames in memory rather than reading every well from disk each time. On a
    plate that fits the row-cache budget that is the plate; on one that does not
    it is part of it, and a count over part of a plate printed like a count over
    all of it is the silent shortfall CLAUDE.md §7 forbids.
    """
    dataset = ctx.state.dataset
    total = len(dataset.well_paths)
    held = sum(1 for label in dataset.cache.keys() if label in dataset.well_paths)
    if total == 0 or held >= total:
        return ""
    return f" — counted over {held} of {total} wells in memory"


def record_fov_verdict(ctx, label: str, df) -> None:
    """Judge one well's ``fov`` column and store the verdict.

    Every per-field number in the app groups on that column, and a well whose
    filenames did not match the schema arrives with every row labelled field
    "1" — indistinguishable, after loading, from a well that genuinely has one
    field. So the check runs once, as the frame is built.

    A function rather than four lines inside :func:`get_rows`, because
    ``get_rows`` is not the only way a frame is built. ``load_controller.
    load_directory`` fills ``dataset.cache`` itself, well by well, and so never
    reached this — which left ``dataset.fov_integrity`` empty for the whole
    session and ``_report_unusable_fov_labels`` with nothing to report. The
    warning existed, was tested, and could not fire. Any future path that
    builds a frame calls this too, or it inherits the same silence.
    """
    record_fov_integrity(ctx, label, check_fov_labels(df))


def record_fov_integrity(ctx, label: str, verdict) -> None:
    """Store a well's FOV-label verdict, and warn when the labels cannot be trusted.

    The half of :func:`record_fov_verdict` that does not need the rows: a well
    opened from its summary (``review/data/well_summary.py``) carries the
    verdict computed when the summary was.
    """
    ctx.state.dataset.fov_integrity[label] = verdict
    if not verdict.trusted:
        _logger.warning(
            "Well %s: %s. Per-field statistics (error bars, events per "
            "field, the Statistics tab's n) are undefined for this well "
            "until the CSV carries usable FOV labels.", label, verdict.reason,
        )


def get_all_timepoints(ctx) -> list[float]:
    """Unique included timepoints across all loaded wells.

    A dataset-level helper used by several plot tabs (scatter / distribution /
    heat map), so it lives in the data layer rather than any one feature.
    ``include_gate=False`` — the per-cell scatter draws gates, so timepoint
    enumeration must see the full cloud, not a gate-filtered subset.

    A8 residuals-ledger R1: fully ctx — the well list reads typed state and rows
    come from the ctx-native :func:`get_rows` service, so no god-object residual.
    """
    timepoints: set = set()
    for label in ctx.state.dataset.well_paths:
        df = get_rows(ctx, label)
        if df is None or df.empty or "timepoint_hours" not in df.columns:
            continue
        mask = df_included_mask(df, include_gate=False)
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").where(mask)
        timepoints.update(float(t) for t in tp.dropna().unique())
    return sorted(timepoints)


def resolve_value_series(
    df: pd.DataFrame,
    key: str,
    ratios: dict[str, RatioMetric] | None = None,
) -> pd.Series:
    """Vectorised counterpart of the old per-row ``resolve_value``.

    For ratio keys (``ratio:<name>``) returns ``num / max(den, epsilon)`` —
    epsilon is the floor the denominator is clamped to, not a term added to
    it, because background-subtracted channels go legitimately negative and
    ``den + epsilon`` sign-flips near zero. ``RatioMetric`` states the rule in
    full. The result is NaN where the clamped denominator is zero or either
    operand is non-finite. For real columns returns the column coerced via
    ``pd.to_numeric``, with non-finite values mapped to NaN.

    Returns NaN-filled Series of the right index when columns/ratios are
    missing.
    """
    if is_ratio_key(key):
        if not ratios:
            return pd.Series(np.nan, index=df.index)
        ratio = ratios.get(key)
        if ratio is None:
            return pd.Series(np.nan, index=df.index)
        ncol, dcol = ratio.numerator_col(), ratio.denominator_col()
        if ncol not in df.columns or dcol not in df.columns:
            return pd.Series(np.nan, index=df.index)
        num = pd.to_numeric(df[ncol], errors="coerce").to_numpy()
        den = pd.to_numeric(df[dcol], errors="coerce").to_numpy()
        # Clamp the denominator to ``epsilon`` (rather than adding
        # epsilon). Background-subtracted channels can go slightly
        # negative; ``den + epsilon`` was sign-flipping near zero and
        # producing arbitrarily large ratios for individual cells. The
        # clamp keeps the divisor strictly positive when epsilon > 0
        # and preserves the old NaN-on-zero behaviour when epsilon == 0.
        if ratio.epsilon > 0.0:
            denom = np.maximum(den, ratio.epsilon)
        else:
            denom = den
        finite = np.isfinite(num) & np.isfinite(den)
        nz = denom != 0.0
        with np.errstate(divide="ignore", invalid="ignore"):
            val = np.where(finite & nz, num / np.where(nz, denom, 1.0), np.nan)
        return pd.Series(val, index=df.index)

    if key not in df.columns:
        # Case-insensitive fallback: load_well_csv now normalises measurement
        # columns to lower-case, but older cached frames or upstream callers
        # may still hold mixed-case headers. Resolve by case-folded match
        # before declaring the column missing.
        lc = key.lower()
        match = next((c for c in df.columns if c.lower() == lc), None)
        if match is None:
            # Key the dedupe by (column, schema fingerprint) rather than
            # ``id(df)`` — object ids are reused after GC, which could suppress
            # a legitimately-new warning (or fail to), and grow the set
            # unbounded across a session.
            warn_key = f"resolve_value_series::{key}::{hash(tuple(sorted(df.columns)))}"
            if warn_key not in _missing_col_warned:
                _missing_col_warned.add(warn_key)
                _logger.warning(
                    "resolve_value_series: column %r not found in DataFrame "
                    "(id=%s, %d cols); returning NaN-filled series. Columns: %s",
                    key, id(df), len(df.columns), sorted(df.columns.tolist()),
                )
            return pd.Series(np.nan, index=df.index)
        key = match
    v = pd.to_numeric(df[key], errors="coerce")
    return v.where(np.isfinite(v), np.nan)


# ── Aggregation ──────────────────────────────────────────────────────────────

# (time_h, mean_above_threshold, sd_above, fraction_above, n_above, n_total,
#  frac_spread, n_above_per_fov_mean, n_above_per_fov_spread)
# n_above                 : cells above threshold at this timepoint  → plot 1 denominator
# n_total                 : all cells at this timepoint              → plot 2 denominator
# frac_spread             : SD/SEM of the fraction across per-FOV fractions when
#                           per_fov_spread is enabled, else 0.0.
# n_above_per_fov_mean    : when per_fov_spread is enabled, mean of per-FOV
#                           above-threshold counts; else 0.0.
# n_above_per_fov_spread  : when per_fov_spread is enabled, SD/SEM of those
#                           per-FOV counts (the "Aggregate FOVs" toggle uses
#                           these to make the events panel show per-FOV mean ±
#                           error instead of a single total bar); else 0.0.
# Trailing positions so existing destructuring with `*_` (and `*extra`) keeps
# working.
AggPoint = tuple[float, float, float, float, int, int, float, float, float]


def _ordinal_timepoints_df(df: pd.DataFrame, tp_col: str = "timepoint_hours") -> dict[str, float]:
    """Build a string→ordinal mapping for rows whose numeric timepoint is NaN/missing."""
    if len(df) == 0 or "timepoint" not in df.columns:
        return {}
    if tp_col in df.columns:
        tp_num = pd.to_numeric(df[tp_col], errors="coerce").to_numpy()
    else:
        tp_num = np.full(len(df), np.nan)
    tp_str = timepoint_text(df).to_numpy()
    not_finite = ~np.isfinite(tp_num)
    raw_strings: set[str] = set()
    for s in tp_str[not_finite]:
        s_str = str(s)
        if s_str and parse_timepoint_hours(s_str) is None:
            raw_strings.add(s_str)
    return {s: float(i) for i, s in enumerate(sorted(raw_strings))}


def resolved_timepoints(
    df: pd.DataFrame,
    tp_col: str = "timepoint_hours",
    *,
    rows_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Each row's timepoint in hours, falling back to the string column.

    The numeric ``timepoint_hours`` column is authoritative. Rows where it is
    missing are resolved from the text ``timepoint`` column — parsed if it
    parses, otherwise numbered through :func:`_ordinal_timepoints_df` — because
    a plate whose filenames carry an unparseable token still has to plot.

    *rows_mask* is the rows whose timepoint can actually reach the output, and
    it is a performance contract rather than a filter: **the decision to run the
    string fallback at all is made against those rows only.** The fallback is
    two full-column passes, each widening a categorical to object and stripping
    every row, and on a plate whose numeric column is complete neither result is
    ever read — they were around nine tenths of
    :func:`aggregate_with_threshold_df`'s cost. Testing every row instead hands
    the whole well to the slow path for one *excluded* row carrying a bad token,
    and the pipeline writes ``nan`` for exactly that, so real plates do carry
    them. Passing ``None`` tests every row, which is what a caller with no
    inclusion mask of its own must do.

    Returns a float array the length of *df*; unresolvable rows are ``NaN``.
    """
    n = len(df)
    if tp_col in df.columns:
        tp_num = pd.to_numeric(df[tp_col], errors="coerce").to_numpy().astype(float)
    else:
        tp_num = np.full(n, np.nan)

    need = ~np.isfinite(tp_num)
    probe = need if rows_mask is None else need[rows_mask]
    if not probe.any():
        return tp_num

    ord_map = _ordinal_timepoints_df(df, tp_col)
    tp_str = timepoint_text(df).to_numpy()
    cache: dict[str, float] = {}
    for s in np.unique(tp_str[need]):
        s_str = str(s)
        if s_str == "":
            cache[s_str] = 0.0
        else:
            v = parse_timepoint_hours(s_str)
            if v is None:
                v = ord_map.get(s_str)
            cache[s_str] = float(v) if v is not None else float("nan")
    for i in np.flatnonzero(need):
        tp_num[i] = cache[str(tp_str[i])]
    return tp_num


def aggregate_with_threshold_df(
    df: pd.DataFrame,
    threshold: float,
    use_sem: bool = False,
    tp_col: str = "timepoint_hours",
    val_col: str = "gfp_mean_intensity",
    fluor_gates: dict[str, float] | None = None,
    per_fov_spread: bool = False,
    ratios: dict[str, RatioMetric] | None = None,
    on_col: str | None = None,
) -> list[AggPoint]:
    """Group rows by timepoint; compute stats for cells above threshold.

    Applies consistent gating across all rows up front — the inclusion chokepoint
    ``df_included_mask`` (``Included==1 AND QC_Pass==1 AND Crop_Pass==1``) plus
    the cell-area threshold and per-channel fluorescence gates — then computes
    statistics on the filtered cell population.

    When ``per_fov_spread`` is True, the spread on the mean is the SD/SEM
    across per-FOV mean intensities at each timepoint, the ``frac_spread`` is
    the SD/SEM across per-FOV ``n_above/n_total`` ratios, and the trailing
    fields summarise per-FOV above-threshold counts.
    """
    if df is None or len(df) == 0:
        return []
    if fluor_gates is None:
        fluor_gates = {}

    # Inclusion must go through the single chokepoint so the bar/line aggregation
    # honors the same gating as every other plot/stat: effective inclusion is
    # ``Included==1 AND QC_Pass==1 AND Crop_Pass==1``. Filtering ``Included``
    # alone here silently bypassed the QC and crop layers.
    mask = df_included_mask(df).to_numpy(copy=True)

    if "area_px" in df.columns:
        area = pd.to_numeric(df["area_px"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            # Match _all_fluor_values_filtered: drop NaN areas explicitly
            # rather than letting `~(NaN <= x)` keep them. Without this,
            # aggregation kept NaN-area cells but raw filtering dropped
            # them — same dataset, different counts.
            mask &= np.isfinite(area)
    else:
        _warn_no_area_column("aggregate_with_threshold_df")
        return []

    for channel, gate in active_fluor_gates(fluor_gates).items():
        col = f"{channel}_mean_intensity"
        if col not in df.columns:
            if col not in _missing_col_warned:
                _missing_col_warned.add(col)
                _logger.warning(
                    "Cell-gating channel %r references column %r which is "
                    "absent from the loaded CSVs — aggregation is returning "
                    "no rows for this channel. Check the gating thresholds "
                    "on Home → Cell Gating against the "
                    "current dataset's schema.",
                    channel, col,
                )
            return []
        v = pd.to_numeric(df[col], errors="coerce").to_numpy()
        mask &= np.isfinite(v) & (v > gate)

    if not mask.any():
        return []

    tp_num = resolved_timepoints(df, tp_col, rows_mask=mask)

    val = resolve_value_series(df, val_col, ratios).to_numpy()
    if val.size == 0:
        return []

    final = mask & np.isfinite(tp_num) & np.isfinite(val)
    if not final.any():
        return []

    tp_arr = tp_num[final]
    val_arr = val[final]

    # Scatter-gate "thresholding" mode: a per-row boolean column defines the
    # on-population (fraction-on by gate) instead of ``value > threshold``.
    on_arr = None
    if on_col and on_col in df.columns:
        on_full = pd.to_numeric(df[on_col], errors="coerce").fillna(0).to_numpy() > 0.5
        on_arr = on_full[final]

    # Per-field statistics are only meaningful if the FOV labels actually
    # identify the fields. A well whose filenames didn't match the schema
    # arrives with every row labelled field "1" (parse_filename writes "" for a
    # slot it can't fill; load_well_csv maps "" and "-1" onto "1"), which is
    # indistinguishable *after* loading from a well that genuinely has one
    # field. Checking it here — against the source images and nucleus ids the
    # rows already carry — is what stops the pooled total from being reported as
    # a per-field mean.
    fov_trusted = True
    if per_fov_spread:
        fov_arr = fov_text(df).to_numpy()[final]
        verdict = check_fov_labels(df)
        fov_trusted = verdict.trusted
        if not fov_trusted:
            _logger.warning(
                "FOV labels are unusable for this well (%s) — per-field "
                "statistics are reported as undefined rather than pooled.",
                verdict.reason,
            )
    else:
        fov_arr = None

    return _aggregate_arrays(tp_arr, val_arr, fov_arr, threshold, use_sem,
                             per_fov_spread, on_arr=on_arr,
                             fov_trusted=fov_trusted)


def _aggregate_arrays(
    tp_arr: np.ndarray,
    val_arr: np.ndarray,
    fov_arr: np.ndarray | None,
    threshold: float,
    use_sem: bool,
    per_fov_spread: bool,
    on_arr: np.ndarray | None = None,
    fov_trusted: bool = True,
) -> list[AggPoint]:
    """Per-timepoint mean / fraction of the "on" population.

    "On" is normally ``value > threshold``; when ``on_arr`` is given (a per-row
    boolean aligned to ``val_arr``) it defines the on-population instead — the
    scatter-gate "thresholding" mode passes gate membership here so fraction-on
    (and the mean of the on-population) reflect the gate rather than the
    per-channel ThreshFracOn cutoff.

    **Every spread this returns is NaN when it is undefined, never 0.0.** A
    spread of zero is a measurement — it says the replicates agreed exactly —
    and one replicate is not that. Reporting the two identically is what let a
    well with a single field (or with unusable field labels, see
    ``fov_trusted``) draw exactly like a well whose error bars had been turned
    off, and made the exported ``err_*`` column read ``0.000000`` for a quantity
    that was never computed. Consumers already skip a non-positive spread, so
    NaN drops the error bar the same way while staying visibly undefined.

    ``fov_trusted=False`` (the caller found the FOV labels don't identify the
    fields) additionally makes the per-field *event count* undefined rather than
    the pooled total, which is otherwise six fields' events plotted as if it
    were one field's mean.
    """
    result: list[AggPoint] = []
    for t in np.unique(tp_arr):
        idx = tp_arr == t
        v = val_arr[idx]
        n_total = int(v.size)
        above_mask = on_arr[idx] if on_arr is not None else (v > threshold)
        above = v[above_mask]
        n_above = int(above.size)
        mean = float(above.sum() / n_above) if n_above else float("nan")

        nan = float("nan")
        spread = nan
        frac_spread = nan
        n_above_per_fov_mean = nan
        n_above_per_fov_spread = nan

        if per_fov_spread and not fov_trusted:
            pass  # labels don't identify fields — every per-field value stays NaN
        elif per_fov_spread:
            f = fov_arr[idx]
            sub = pd.DataFrame({"fov": f, "v": v, "above": above_mask})
            grp = sub.groupby("fov", sort=False)
            fov_total = grp.size()
            fov_above_count = grp["above"].sum().astype(int)
            fov_mean = sub.loc[above_mask].groupby("fov", sort=False)["v"].mean()

            valid_fov = fov_total > 0
            fov_total = fov_total[valid_fov]
            fov_above_count = fov_above_count.reindex(fov_total.index, fill_value=0)
            fov_mean = fov_mean.reindex(fov_total.index)

            # Per-FOV spreads treat each FOV as a sample, so use the
            # sample SD (ddof=1) consistently — matches what the Stats
            # tab reports for the same groups. The per-cell SD branch
            # below (line ~454) stays ddof=0 because it's an explicit
            # population SD of cells around the well mean (labelled
            # accordingly in the axis title).
            fov_means = fov_mean.dropna().to_numpy()
            if fov_means.size > 1:
                sd = float(fov_means.std(ddof=1))
                spread = sd / math.sqrt(fov_means.size) if use_sem else sd

            fov_fracs = (fov_above_count.to_numpy() / fov_total.to_numpy()).astype(float)
            if fov_fracs.size > 1:
                fsd = float(fov_fracs.std(ddof=1))
                frac_spread = fsd / math.sqrt(fov_fracs.size) if use_sem else fsd

            fov_n_above = fov_above_count.to_numpy().astype(float)
            if fov_n_above.size >= 1:
                n_above_per_fov_mean = float(fov_n_above.sum() / fov_n_above.size)
            if fov_n_above.size > 1:
                nsd = float(fov_n_above.std(ddof=1))
                n_above_per_fov_spread = (nsd / math.sqrt(fov_n_above.size)
                                          if use_sem else nsd)
        else:
            if n_above > 1:
                m = above.sum() / n_above
                sd = float(np.sqrt(((above - m) ** 2).sum() / n_above))
                spread = sd / math.sqrt(n_above) if use_sem else sd

        result.append((float(t), mean, float(spread),
                       n_above / n_total if n_total else float("nan"),
                       int(n_above), int(n_total), float(frac_spread),
                       float(n_above_per_fov_mean),
                       float(n_above_per_fov_spread)))
    return result


def _all_fluor_values(df: pd.DataFrame, val_col: str = "gfp_mean_intensity") -> np.ndarray:
    """Return the Included rows' values for ``val_col`` as a numpy array."""
    if df is None or df.empty or val_col not in df.columns:
        return np.empty(0, dtype=float)
    mask = df_included_mask(df).to_numpy(copy=True)
    v = pd.to_numeric(df[val_col], errors="coerce").to_numpy()
    finite = np.isfinite(v)
    return v[mask & finite]


def _all_fluor_values_filtered(
    df: pd.DataFrame,
    val_col: str = "gfp_mean_intensity",
    fluor_gates: dict[str, float] | None = None,
    ratios: dict[str, RatioMetric] | None = None,
    tp_filter: float | None = None,
    tp_col: str = "timepoint_hours",
) -> np.ndarray:
    """Vectorised filter+resolve. Mirrors the legacy scalar filter exactly."""
    if df is None or df.empty:
        return np.empty(0, dtype=float)
    if fluor_gates is None:
        fluor_gates = {}

    mask = df_included_mask(df).to_numpy(copy=True)

    if "area_px" in df.columns:
        area = pd.to_numeric(df["area_px"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(area)
    else:
        _warn_no_area_column("all_fluor_values_filtered")
        return np.empty(0, dtype=float)

    for channel, gate in active_fluor_gates(fluor_gates).items():
        col = f"{channel}_mean_intensity"
        if col not in df.columns:
            return np.empty(0, dtype=float)
        v = pd.to_numeric(df[col], errors="coerce").to_numpy()
        mask &= np.isfinite(v) & (v > gate)

    if tp_filter is not None:
        if tp_col not in df.columns:
            return np.empty(0, dtype=float)
        tp = pd.to_numeric(df[tp_col], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(tp) & tp_close_mask(tp, tp_filter)

    if not mask.any():
        return np.empty(0, dtype=float)

    val = resolve_value_series(df, val_col, ratios).to_numpy()
    mask &= np.isfinite(val)
    return val[mask]


# ── Beeswarm jitter ──────────────────────────────────────────────────────────

def _beeswarm_jitter(
    values: list[float],
    x_center: float = 0.0,
    max_spread: float = 0.35,
    n_bins: int = 40,
) -> tuple[list[float], list[float]]:
    """Standard (non-overlapping) beeswarm x-positions for a column.

    Each point keeps its real y value and is nudged sideways to the position
    *closest to the column midline* that doesn't collide with an
    already-placed point — the classic "swarm" packing used by R's ``beeswarm``
    / seaborn ``swarmplot``. Points are placed in ascending-y order; for the
    new point, every placed point within one dot-diameter ``d`` in y carves out
    a forbidden x-interval, and the new point takes the nearest free x. The
    column therefore fans out where points are dense and stays slim in the
    tails, with no overlaps and no x↔y correlation (so no chevron / "V").

    Placement runs in a normalised space (y scaled to ``[0, 1]``, dot diameter
    ``d = 1/n_bins``); the resulting offsets are then scaled so the densest part
    of the column just fills ``max_spread`` (its half-slot width). Deterministic.
    """
    arr = np.asarray(values, dtype=float)
    n = arr.size
    if n == 0:
        return [], []
    if n == 1:
        return [x_center], [float(arr[0])]

    lo = float(arr.min())
    hi = float(arr.max())
    span = hi - lo if hi > lo else 1.0
    yn = (arr - lo) / span                       # normalised y in [0, 1]
    d = 1.0 / float(max(n_bins, 1))              # dot diameter (normalised)
    d2 = d * d

    # Very dense columns can't be packed without overlap (and the swarm loop is
    # superlinear) — fall back to a density-weighted jitter, which still reads
    # as a swarm and shows every point.
    if n > 6000:
        bin_idx = np.minimum(np.digitize(arr, lo + (span / n_bins) * np.arange(1, n_bins)),
                             n_bins - 1)
        counts = np.bincount(bin_idx, minlength=n_bins)
        half = (counts[bin_idx] / max(int(counts.max()), 1)) * max_spread
        xs = x_center + np.random.default_rng(0).uniform(-1.0, 1.0, size=n) * half
        return xs.tolist(), arr.tolist()

    order = np.argsort(yn, kind="mergesort")     # ascending y, stable
    offs = np.zeros(n, dtype=float)
    placed_x: list[float] = []
    placed_y: list[float] = []                   # ascending (insertion order)
    win = 0                                       # sliding-window start
    bal = 0.0                                     # running L/R balance (symmetry)
    for idx in order:
        y = float(yn[idx])
        while win < len(placed_y) and placed_y[win] <= y - d:
            win += 1
        # Forbidden x-intervals from still-active placed points (|Δy| < d).
        intervals: list[tuple[float, float]] = []
        for j in range(win, len(placed_y)):
            dy = y - placed_y[j]
            g = d2 - dy * dy
            if g > 0.0:
                w = math.sqrt(g)
                px = placed_x[j]
                intervals.append((px - w, px + w))
        if not intervals:
            chosen = 0.0
        else:
            intervals.sort()
            merged = [list(intervals[0])]
            for a, b in intervals[1:]:
                if a <= merged[-1][1] + 1e-12:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            cover = next(((a, b) for a, b in merged
                          if a - 1e-12 <= 0.0 <= b + 1e-12), None)
            if cover is None:
                chosen = 0.0                       # midline is free
            else:
                a, b = cover                        # nearest free spot each side
                if abs(b) < abs(a) - 1e-12:
                    chosen = b
                elif abs(a) < abs(b) - 1e-12:
                    chosen = a
                else:                               # tie → balance the column
                    chosen = b if bal <= 0.0 else a
        offs[idx] = chosen
        bal += chosen
        placed_x.append(chosen)
        placed_y.append(y)

    m = float(np.max(np.abs(offs)))
    if m > 0.0:
        offs *= max_spread / m
    xs = x_center + offs
    return xs.tolist(), arr.tolist()


# ── Well-token parsing ───────────────────────────────────────────────────────

def extract_well_token(label: str) -> str | None:
    """'measurements_B10' → 'B10'."""
    m = re.search(r"([A-Ha-h])(\d{1,2})$", label)
    return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else None


def parse_well_token(token: str) -> tuple[int, int] | None:
    """Convert a well token like ``"A01"`` into ``(row, col)`` zero-based."""
    if not token:
        return None
    m = re.fullmatch(r"\s*([A-Za-z])(\d{1,2})\s*", str(token))
    if not m:
        return None
    row = ord(m.group(1).upper()) - ord("A")
    col = int(m.group(2)) - 1
    if row < 0 or col < 0:
        return None
    return row, col


# ── Plot-group iteration ─────────────────────────────────────────────────────

def plot_group_well_lists(ctx, fallback_to_all: bool = True) -> list[tuple[str, list[str], Any]]:
    """The wells each plotted group is drawn from: ``[(name, [well, …], rset)]``.

    **Which wells a plot is about is decided here and nowhere else.** It is not
    simply "the selected wells": with replicate sets defined the plotted set is
    every non-hidden group's wells *plus* the solo wells toggled on via
    ``active_solo_wells``, and ``selected_wells`` is ignored entirely — group
    visibility is the single source of truth in that mode. A second
    implementation of that rule, however careful, produces a plausible result
    with the wrong wells in it: right schema, right-looking counts, no error.
    So :func:`iter_plot_groups` (pooled, for the renderers) and
    :func:`iter_plot_group_wells` (unpooled, for the per-cell export) both call
    this rather than each deciding for themselves, and
    ``tests/test_plot_group_well_lists.py`` holds them to it.

    ``rset`` is the replicate set an entry came from, or ``None`` for an entry
    that is a single well standing for itself — the two colour differently, and
    that is the only reason the caller needs to tell them apart.

    Well *tokens* are what this returns, which the pooled frames cannot give
    back: a pooled frame's ``well`` column is parsed from the image filename and
    need not equal the token the app keys wells by.
    """
    from review.domain.plot_members import plot_replicate_sets

    well_paths = ctx.state.dataset.well_paths or {}
    rep_sets = list(plot_replicate_sets(ctx))
    selected = set(ctx.state.selection.selected_wells or set())

    out: list[tuple[str, list[str], Any]] = []
    if rep_sets:
        in_any_group: set = set()
        for rset in rep_sets:
            wells = [w for w in rset.wells if w in well_paths]
            in_any_group.update(wells)
            if not wells:
                continue
            out.append((rset.name, wells, rset))
        active_solo = ctx.state.selection.active_solo_wells or set()
        for w in sorted(w for w in active_solo
                        if w in well_paths and w not in in_any_group):
            out.append((w, [w], None))
        return out

    if not selected:
        if not fallback_to_all:
            return out
        selected = set(well_paths.keys())
    for w in sorted(selected):
        if w not in well_paths:
            continue
        out.append((w, [w], None))
    return out


def iter_plot_groups(ctx, fallback_to_all: bool = True) -> Iterator[tuple[str, str, pd.DataFrame]]:
    """Yield ``(name, color, df)`` for each replicate set or selected well.

    When replicate sets are defined, one entry per replicate set with rows
    pooled (``pd.concat``) from all wells in the set; otherwise one entry per
    selected well. When ``fallback_to_all`` is True (default) and no wells are
    selected, all loaded wells are yielded.

    A8 teardown: takes ``ctx`` — the well set / selection come from typed state,
    rows from the ctx-native :func:`get_rows` (R1), and colours from the pure
    ``color_for_replicate_set`` / ``color_for_wells`` graph. The old ``_color_for_label``
    / ``_color_for_well`` override hooks were vestigial (never set anywhere) and
    are dropped. Colours are identical to the sidebar plate map + the Line / Bar /
    Scatter renderers (locked by test_iter_plot_groups_ctx).

    Which wells each group holds comes from :func:`plot_group_well_lists`; only
    the pooling and the colours are this function's own.
    """
    from review.domain.selections_model import (
        _DEFAULT_COLOR,
        color_for_replicate_set,
        color_for_wells,
    )

    selections = ctx.state.selection.selections

    def _color_rset(rset) -> str:
        try:
            return color_for_replicate_set(rset, selections)
        except Exception:
            swallowed()  # was: pass (silent except)
        return _DEFAULT_COLOR

    for name, wells, rset in plot_group_well_lists(ctx, fallback_to_all):
        frames = [get_rows(ctx, w) for w in wells]
        pooled = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        color = _color_rset(rset) if rset is not None else color_for_wells([name])
        yield name, color, pooled


def iter_plot_group_wells(
    ctx, fallback_to_all: bool = True,
) -> Iterator[tuple[str, str, pd.DataFrame]]:
    """Yield ``(group_name, well_label, df)`` — one entry per well, unpooled.

    The per-cell export's view of the same selection :func:`iter_plot_groups`
    draws from. It exists because the pooled generator loses two things the
    export needs: the well token (a pooled frame carries only the
    filename-parsed ``well`` column) and a usable row identity, since
    ``pd.concat(..., ignore_index=True)`` renumbers a multi-well group while a
    one-well group keeps the cached frame's own index — the same field meaning
    two different things depending on how many wells a group happens to hold.
    """
    for name, wells, _rset in plot_group_well_lists(ctx, fallback_to_all):
        for well in wells:
            yield name, well, get_rows(ctx, well)
