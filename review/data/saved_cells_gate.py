"""The one saved-cell set left out of every analysis, projected as ``Saved_Cells_Pass``.

Saving cells changes no number; choosing a set under Cell Gating → *Exclude
saved cells* does (``core/cell_sets.set_excluded``). This registered cache
projector writes ``Saved_Cells_Pass`` = 0 for that set's usable cells (stale
cells in re-segmented wells are not excluded — they may be other cells now) and
drops the column when no set is excluded, so switching it off restores every
cell. One global slot, not one verdict per set: a verdict removes cells from
every aggregate, so a set that only marks or colours must never become one.
"""

from __future__ import annotations

import pandas as pd

from domain.image_identity import cell_key_tuples_for_dataframe

#: The verdict column (``data_loading.VERDICT_LAYERS``).
SAVED_CELLS_COLUMN = "Saved_Cells_Pass"


def saved_cells_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """Nothing beyond the identity columns every narrow read carries."""
    return ()


def _apply(df, cells: frozenset, well: str) -> None:
    if not cells:
        if SAVED_CELLS_COLUMN in df.columns:
            df.drop(columns=[SAVED_CELLS_COLUMN], inplace=True)
        return
    keys = cell_key_tuples_for_dataframe(df)
    if keys is None:
        return
    hit = keys.map(lambda k: (well, *k) in cells)
    df[SAVED_CELLS_COLUMN] = (~pd.Series(hit, index=df.index).astype(bool)).astype(int)


def apply_saved_cells_gate_to_cache(ctx, label: str | None = None) -> None:
    """Project the excluded set onto *label*'s cached rows, or every held well."""
    from review.core.cell_sets import excluded_cells
    from review.data.data_loading import get_rows

    cells = excluded_cells(ctx)
    wells = [label] if label is not None else list(ctx.state.dataset.well_paths or ())
    for well in wells:
        if label is None:
            repo = getattr(ctx.state, "repo", None)
            df = repo.rows_or_none(well) if repo is not None else None
        else:
            df = get_rows(ctx, well)
        if df is None or df.empty:
            continue
        _apply(df, frozenset(c for c in cells if c[0] == well) if cells else frozenset(), well)
