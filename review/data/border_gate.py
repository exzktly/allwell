"""Exclude the cells the field of view cuts, and say what that did.

The pipeline writes one flag per cell — ``qc_touches_border``
(:data:`pipeline.shape_features.BORDER_COLUMN`), 1 when any pixel of the cell's
mask lies on the image's outermost row or column. That is MATLAB's
``imclearborder`` rule applied to the label image. This module turns the flag
into a ``Border_Pass`` verdict the inclusion chokepoint
(:func:`review.data.data_loading.df_included_mask`) ANDs with its siblings.

**Why exclude them at all.** A cell the image edge cuts is partial: its area,
its intensity totals and every shape metric describe the visible fragment, not
the cell. Totals and areas are biased low and circularity is wrong, so a plate
imaged at high density carries a population of systematically-wrong rows along
every field's edge.

**Why it is on by default, except for nanowell runs.** For an ordinary plate
a partial cell is a wrong row, so the default drops it
(:func:`review.domain.qc_params.default_exclude_border_cells`). A nanowell
run's image is tiled across a lattice of wells: a tile edge is not a well edge,
and the nanowell measurement assigns cells to wells itself, so the default
there is off. The dataset kind is read from the run's own sidecar.

**Why a layer of its own rather than a fifth debris bound.** The debris gate's
verdict (``QC_Pass``) is labelled "debris gate" on every surface that explains
an exclusion — the Segmentation hover, the inclusion funnel. A partial cell is
not debris, and folding it into that column would have the hover name the
wrong reason for every one of them. A separate column keeps each exclusion
attributable to the rule that made it.

**Three behaviours that are deliberate, each because the alternative is a
silent substitution.**

* A dataset with **no column** (analysed before it was emitted) gets no
  ``Border_Pass`` column, and the panel says the dataset predates the
  measurement and that a re-run records it — rather than writing an all-pass
  column that would assert the plate was checked and found clean.
* A missing value keeps the row (``fillna(1)`` at the chokepoint): a cell
  nobody measured is not a cell measured and found wanting.
* Switched off, the column is **removed** from every cached frame rather than
  simply not written, so a frame does not go on being filtered by a control
  the user has just unticked (``cache_projectors.drop_verdict_column``).

Qt-free: this is the data layer. It imports the pipeline's column name and the
domain's parameters and nothing above either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from review.data.cache_projectors import drop_verdict_column, project_verdict_column
from review.data.plate_scan import every_well

if TYPE_CHECKING:  # annotations only — the data layer does not import pandas
    import pandas as pd  # noqa: F401   at module scope

#: The column this writes, named like its siblings (``QC_Pass``, ``Focus_Pass``).
BORDER_PASS_COLUMN = "Border_Pass"


def _params(ctx):
    """The loaded dataset's :class:`~review.domain.qc_params.QCParams`, read
    straight off typed state (``data/`` may not import the QC feature)."""
    qc = getattr(ctx.state, "qc", None)
    return getattr(qc, "params", None)


def gate_is_on(ctx) -> bool:
    """Whether the user has asked for border-touching cells to be excluded."""
    params = _params(ctx)
    return bool(params is not None and params.exclude_border_cells)


def border_flags(df) -> pd.Series | None:
    """The frame's border flag as floats, or ``None`` when it has no column.

    ``None`` (a dataset analysed before the column existed) is a different
    answer from a column of zeros (measured, nothing touches the edge).
    """
    from pipeline.shape_features import BORDER_COLUMN

    if df is None or len(df) == 0 or BORDER_COLUMN not in df.columns:
        return None
    import pandas as pd

    return pd.to_numeric(df[BORDER_COLUMN], errors="coerce")


def compute_border_pass(df, params) -> pd.Series:
    """``1`` where a cell is whole, ``0`` where the image border cuts it.

    Every row passes when the switch is off, when the column is absent, and
    wherever the flag is missing — an unmeasured cell is kept, not judged.
    """
    import pandas as pd

    passed = pd.Series(1, index=df.index, dtype=int)
    if params is None or not params.exclude_border_cells:
        return passed
    flags = border_flags(df)
    if flags is None:
        return passed
    touching = flags.notna() & flags.eq(1)
    return passed.where(~touching, 0)


def candidate_rows(df):
    """The rows the count is about: what the analysis keeps with this layer off.

    Composed from the chokepoint minus ``Border_Pass`` — not every row of the
    frame — so the panel's "N cells touch the border" does not count debris the
    QC gate has already dropped, and does not fall to zero the moment this
    gate is switched on and starts filtering its own evidence.
    """
    from review.data.data_loading import df_included_mask

    if df is None or len(df) == 0:
        return df
    return df.loc[df_included_mask(df, except_layer=BORDER_PASS_COLUMN)]


def border_counts(df) -> tuple[int, int]:
    """``(touching, total)`` over one frame's :func:`candidate_rows`.

    ``total`` counts the candidate rows whether or not the frame carries the
    column, so a mixed plate (some wells analysed before the column existed)
    is reported over the cells that could be judged — see
    :func:`border_status`.
    """
    rows = candidate_rows(df)
    if rows is None or len(rows) == 0:
        return (0, 0)
    flags = border_flags(rows)
    if flags is None:
        return (0, 0)
    return (int((flags.notna() & flags.eq(1)).sum()), int(len(rows)))


def plate_border_counts(ctx) -> tuple[int, int]:
    """``(touching, total)`` summed over every well of the plate, held or not.

    Counted whether or not the gate is on, so the panel can say what ticking
    the box *would* do before it is ticked.
    """
    touching = total = 0
    for t, n in every_well(ctx, "border_counts", None, border_counts).values():
        touching += t
        total += n
    return (touching, total)


def dataset_has_border_column(ctx) -> bool | None:
    """Whether the dataset carries the column — ``None`` when it cannot be told.

    ``None`` when there is nothing to look at (a reset just emptied the row
    cache and no summaries are held): saying "this dataset predates the
    measurement" about one that does not would name a remedy — a re-run — that
    is not needed.
    """
    from pipeline.shape_features import BORDER_COLUMN

    summaries = getattr(ctx.state.dataset, "summaries", None) or {}
    if summaries:
        return any(BORDER_COLUMN in s.columns for s in summaries.values())
    cache = getattr(ctx.state.dataset, "cache", None) or {}
    frames = [df for df in cache.values() if df is not None and len(df)]
    if not frames:
        return None
    return any(BORDER_COLUMN in getattr(df, "columns", ()) for df in frames)


def border_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """The column :func:`apply_border_gate_to_cache`'s verdict reads (``narrow_rows``)."""
    from pipeline.shape_features import BORDER_COLUMN

    return (BORDER_COLUMN,) if gate_is_on(ctx) else ()


def apply_border_gate_to_cache(ctx, label: str | None = None) -> list[str]:
    """Write ``Border_Pass`` onto cached well frame(s); drop it when switched off.

    Returns the labels whose verdict could not be computed, as its siblings do.
    """
    if not gate_is_on(ctx):
        drop_verdict_column(ctx, label, column=BORDER_PASS_COLUMN)
        return []
    params = _params(ctx)
    return project_verdict_column(
        ctx, label,
        column=BORDER_PASS_COLUMN,
        verdict=lambda df, _well: compute_border_pass(df, params),
        layer="Image border",
    )


def _cells(n: int) -> str:
    return f"{n:,} cell" if n == 1 else f"{n:,} cells"


def border_status(ctx) -> tuple[str, bool]:
    """``(sentence, is_problem)`` for the Cell Gating panel's border row.

    * nothing loaded to look at → ``""``;
    * no column → the dataset predates the measurement and a re-run records it
      (a problem only when the box is ticked, because then the user has asked
      for something that is not happening);
    * otherwise how many of the analysed cells touch the border, with what the
      switch is doing to them right now.
    """
    has_column = dataset_has_border_column(ctx)
    if has_column is None:
        return ("", False)
    if not has_column:
        return ("This dataset was analysed before border contact was recorded, "
                "so nothing can be excluded — re-run the analysis to record it.",
                gate_is_on(ctx))
    touching, total = plate_border_counts(ctx)
    if not total:
        return ("", False)
    if not touching:
        return (f"None of the {_cells(total)} touch the image border.", False)
    touch = "touches" if touching == 1 else "touch"
    verb = "excluded" if gate_is_on(ctx) else "kept"
    return (f"{_cells(touching)} of {total:,} {touch} the image border — "
            f"currently {verb}, across the plate.", False)
