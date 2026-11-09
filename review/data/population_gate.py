"""The size class the analysis is restricted to, projected as ``Population_Pass``.

Cell Gating's size classes (``review.domain.size_classes``) name two populations told apart by area — *T cells* and
*DCs*, say. Choosing one of them in the Plotting band's **Population** control (``QCParams.population``) restricts
the whole analysis to it: this registered cache projector writes ``Population_Pass`` = 1 for each row in the chosen
class and 0 for every other, so the chokepoint every plot, statistic, threshold count, fold change and export reads
(``data_loading.df_included_mask``) leaves the rest out. With no class chosen, or a rule that is not complete, the
column is dropped and every cell counts again.

**A row is judged on the debris gate's own area column** (``size_classes.area_column``), the one the classes are
defined in, so the population means the same pixels on the panel and in the plots. A row with no area — the column is
missing from the well's table, or the value is blank — cannot be shown to be in the class, so it is left out: a
population that quietly took in cells nobody could class would be a different population from the one named on the
figure. :func:`unclassable_wells` names the wells that lost every row that way, for the figure footer.

Not to be confused with the per-nanowell class *counts*, which must see both classes and so read the inclusion with
this layer left out (``nanowell.controller.gate_tally``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: The verdict column (``data_loading.VERDICT_LAYERS``).
POPULATION_COLUMN = "Population_Pass"


def _chosen(ctx):
    """``(rule, class index)`` in force, or ``None`` (``QCParams.population_rule``), read through the QC layer's own
    accessor so a dataset whose parameters are not yet seeded reads its saved choice."""
    features = getattr(ctx, "features", None)
    params = features.query("qc.get_params", ctx, default=None) if features is not None else None
    if params is None:
        qc = getattr(getattr(ctx, "state", None), "qc", None)
        params = getattr(qc, "params", None)
    rule_of = getattr(params, "population_rule", None)
    return rule_of() if callable(rule_of) else None


def population_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """The area columns a class is judged on, while a class is chosen."""
    from review.domain.qc_params import AREA_COLS

    return tuple(AREA_COLS) if _chosen(ctx) is not None else ()


def population_mask(df: pd.DataFrame, rule, k: int) -> np.ndarray:
    """Whether each row of *df* is in class *k* of *rule*; every row out when the table has no area column."""
    from review.domain.size_classes import classify_rows

    classes = classify_rows(df, rule)
    if classes is None:
        return np.zeros(len(df), dtype=bool)
    return np.asarray(classes) == int(k)


def _apply(df: pd.DataFrame, chosen) -> None:
    if chosen is None:
        if POPULATION_COLUMN in df.columns:
            df.drop(columns=[POPULATION_COLUMN], inplace=True)
        return
    rule, k = chosen
    df[POPULATION_COLUMN] = population_mask(df, rule, k).astype(int)


def apply_population_to_cache(ctx, label: str | None = None) -> None:
    """Project the chosen class onto *label*'s cached rows, or every held well."""
    from review.data.data_loading import get_rows

    chosen = _chosen(ctx)
    wells = [label] if label is not None else list(ctx.state.dataset.well_paths or ())
    for well in wells:
        if label is None:
            repo = getattr(ctx.state, "repo", None)
            df = repo.rows_or_none(well) if repo is not None else None
        else:
            df = get_rows(ctx, well)
        if df is None or df.empty:
            continue
        _apply(df, chosen)


def unclassable_wells(ctx, wells) -> list[str]:
    """The wells among *wells* whose held rows carry no area column, so no cell of them can be in the chosen class;
    empty with no class chosen. Only held wells are judged — this reads no file."""
    from review.domain.size_classes import area_column

    if _chosen(ctx) is None:
        return []
    repo = getattr(ctx.state, "repo", None)
    out = []
    for well in wells or ():
        df = repo.rows_or_none(well) if repo is not None else None
        if df is not None and not df.empty and area_column(df.columns) is None:
            out.append(str(well))
    return out
