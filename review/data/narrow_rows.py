"""Plate-wide numbers from narrow reads (tracker F2).

A plate-wide aggregate needs a handful of a well's columns — the value it
plots, the timepoint and field labels, and the inclusion verdicts — but
:func:`data_loading.get_rows` reads the whole frame and puts it in the row
cache. On a plate larger than the cache that is a full Parquet read per well
per aggregate (0.19 s warm, and seconds once the plate no longer fits the page
cache either, for a 1.1M-row well), and every read evicts a well some view was
using. :func:`narrow_rows` reads only the columns the aggregate and the verdicts
need — seven of thirty measured 0.04 s and 36 MiB against 210 MiB — runs the
same verdict projectors on that frame, and hands it back **without** putting it
in the row cache.

**Why this is safe where a narrow frame in the row cache was not (tracker
D2).** The verdicts fail open: a verdict whose input column is absent passes
every cell. So a narrow frame missing one input would be counted as fully
included, and nothing would say so. Here the columns are not a guess: every
registered projector *declares* what its verdict reads under the current
settings (``cache_projectors.register_cache_projector(columns=...)``), the read
takes the union of those declarations with the caller's own, and a projector
that does not declare — or answers ``None`` — sends the read back to the whole
frame. ``tests/test_narrow_rows.py`` holds the declarations to the verdicts:
with every layer active at once, on a fixture where each layer excludes cells
the others keep, the narrow frame's inclusion mask and the aggregate computed
from it equal the full frame's cell for cell.

Every narrow read also carries the identity columns — the text columns, every
spelling of a well, field, timepoint or nucleus id the identity resolver
accepts (matched case-insensitively), ``timepoint_hours`` and ``Included`` — because the curation
keys, the crop scope and the field verdicts all read them, and as categories
they cost almost nothing.

A well already in the row cache is returned as held: it is projected already,
and reading it again would be the work this exists to avoid. With no current
Parquet cache (the first open, or no pyarrow) the read falls back to
:func:`get_rows`, which parses the CSV and fills that cache.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from domain.image_identity import IDENTITY_COLUMN_NAMES
from domain.text_columns import TEXT_COLUMNS
from review.data.cache_projectors import iter_projector_declarations
from review.domain.ratio_models import is_ratio_key

_logger = logging.getLogger("review")

#: Lower-cased names every narrow read carries when the file has them: the text
#: columns, every spelling ``domain.image_identity.resolve_identity_column`` is
#: asked for (``IDENTITY_COLUMN_NAMES``, the one list its callers use), the
#: numeric timepoint, and the ``Included`` layer the projectors write.
IDENTITY_COLUMNS = frozenset({
    *TEXT_COLUMNS, "timepoint_hours", "included",
    *(name.lower() for name in IDENTITY_COLUMN_NAMES),
})

#: What :func:`data_loading.aggregate_with_threshold_df` reads besides the value
#: column, the timepoint column and the verdict columns: the area columns its
#: debris floor reads.
AGGREGATE_COLUMNS = frozenset({"area_px", "shape_area"})


def value_inputs(key: str, ratios: dict | None) -> set[str] | None:
    """The columns ``resolve_value_series(df, key, ratios)`` reads, or ``None``.

    A ratio key reads its numerator and denominator columns; an unknown ratio
    answers ``None`` so the caller reads the whole frame rather than a frame on
    which the ratio resolves to NaN for a different reason than the full one.
    """
    if not key:
        return set()
    if is_ratio_key(key):
        ratio = (ratios or {}).get(key)
        if ratio is None:
            return None
        return {ratio.numerator_col(), ratio.denominator_col()}
    return {key}


def verdict_columns(ctx, label: str) -> set[str] | None:
    """The union of every projector's declared inputs for *label*, or ``None``."""
    wanted: set[str] = set()
    for project, declare in iter_projector_declarations():
        if declare is None:
            _logger.debug("narrow read of %s: %s declares no columns; reading the whole frame",
                          label, getattr(project, "__name__", project))
            return None
        try:
            cols = declare(ctx, label)
        except Exception:
            # A declaration that cannot be answered is one that cannot vouch for
            # the narrow frame: the whole frame is read, where the projector
            # itself runs under ``run_projectors``' guard.
            _logger.exception("narrow read of %s: the column declaration of %s failed; "
                              "reading the whole frame", label, getattr(project, "__name__", project))
            return None
        if cols is None:
            return None
        wanted.update(cols)
    return wanted


def _select(names: list[str], wanted: set[str]) -> list[str]:
    """The file's columns a narrow read takes: *wanted* and the identity columns,
    matched case-insensitively as ``resolve_value_series`` and the identity resolver match."""
    folded = {str(w).strip().lower() for w in wanted} | IDENTITY_COLUMNS
    return [n for n in names if str(n).strip().lower() in folded]


class _Overlay:
    """*base* with some attributes replaced — the scratch context's building block."""

    def __init__(self, base, **replaced):
        self.__dict__["_base"] = base
        self.__dict__.update(replaced)

    def __getattr__(self, name):
        return getattr(self.__dict__["_base"], name)


def _detached(ctx, label: str, frame: pd.DataFrame):
    """A context whose row cache holds *frame* alone, for running the projectors.

    The projectors find a well's frame through ``ctx.state.dataset.cache`` (and
    the gating one through ``ctx.state.repo``), so they are pointed at a cache
    of one; everything else — the settings each verdict reads, the dataset's
    FOV-label verdicts — is the real context's.
    """
    from review.data.well_repository import WellRepository

    dataset = _Overlay(ctx.state.dataset, cache={label: frame})
    state = _Overlay(ctx.state, dataset=dataset)
    scratch = _Overlay(ctx, state=state)
    state.__dict__["repo"] = WellRepository(scratch)
    return scratch


def narrow_rows(ctx, label: str, want: Iterable[str] | None) -> pd.DataFrame:
    """Well *label*'s rows with only the columns *want* and the verdicts need, projected.

    *want* is the caller's own columns (``None``: it cannot say, so the whole
    frame). The result is not put in the row cache, so it is the caller's to
    keep or drop; a held well is returned as held. See the module docstring.
    """
    from review.data.data_loading import get_rows, run_projectors
    from review.data.parquet_cache import cached_column_names, read_cached

    dataset = ctx.state.dataset
    cache = getattr(dataset, "cache", None)
    held = cache.get(label) if cache is not None else None
    if held is not None:
        return held
    path = (getattr(dataset, "well_paths", None) or {}).get(label)
    if want is None or path is None:
        return get_rows(ctx, label)
    verdicts = verdict_columns(ctx, label)
    if verdicts is None:
        return get_rows(ctx, label)
    names = cached_column_names(Path(path))
    if names is None:
        return get_rows(ctx, label)
    frame = read_cached(Path(path), columns=_select(names, set(want) | verdicts))
    if frame is None:
        return get_rows(ctx, label)
    run_projectors(_detached(ctx, label, frame), label)
    return frame
