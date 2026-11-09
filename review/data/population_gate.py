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

The same projector writes each class's **share column** while the rule is complete, whether or not a class is
chosen (``size_classes.share_column``: ``shape_class_share::<label>``, 1 in the class, 0 outside it, blank with no
area). A well's mean of it is that class's share of the well's kept cells, so every plot that takes a per-cell value
offers each class's share — and its fold change — beside the shape features. A share column of a class since renamed
or removed is dropped, so the rows never carry a share the rule no longer defines.

Not to be confused with the per-nanowell class *counts*, which must see both classes and so read the inclusion with
this layer left out (``nanowell.controller.gate_tally``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: The verdict column (``data_loading.VERDICT_LAYERS``).
POPULATION_COLUMN = "Population_Pass"


def _params(ctx):
    """The QC parameters in force, read through the QC layer's own accessor so a dataset whose parameters are not yet
    seeded reads its saved choice; the bare state in a build without that service."""
    from review.core.population_band import qc_params

    params = qc_params(ctx)
    if params is None:
        qc = getattr(getattr(ctx, "state", None), "qc", None)
        params = getattr(qc, "params", None)
    return params


def _chosen(ctx):
    """``(rule, class index)`` in force, or ``None`` (``QCParams.population_rule``)."""
    rule_of = getattr(_params(ctx), "population_rule", None)
    return rule_of() if callable(rule_of) else None


def _rule(ctx):
    """The size-class rule while it is complete, else ``None``."""
    from review.domain.size_classes import SizeClassRule

    rule = getattr(_params(ctx), "size_classes", None)
    return rule if isinstance(rule, SizeClassRule) and rule.active() else None


def population_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """The area columns a class is judged on, while the rule is complete (the share columns are written then, and
    ``Population_Pass`` too when a class is chosen)."""
    from review.domain.qc_params import AREA_COLS

    return tuple(AREA_COLS) if _rule(ctx) is not None else ()


def population_mask(df: pd.DataFrame, rule, k: int) -> np.ndarray:
    """Whether each row of *df* is in class *k* of *rule*; every row out when the table has no area column."""
    from review.domain.size_classes import classify_rows

    classes = classify_rows(df, rule)
    if classes is None:
        return np.zeros(len(df), dtype=bool)
    return np.asarray(classes) == int(k)


def _apply(df: pd.DataFrame, chosen, rule=None) -> None:
    """Write ``Population_Pass`` for *chosen* (``(rule, k)`` or ``None``) and the share columns of *rule* (a complete
    rule or ``None``) onto *df*, dropping whichever does not apply."""
    from review.domain.size_classes import area_column, classify_rows, is_share_column, share_columns

    wanted = share_columns(rule) if rule is not None else []
    stale = [c for c in df.columns if is_share_column(c) and c not in wanted]
    if chosen is None and POPULATION_COLUMN in df.columns:
        stale.append(POPULATION_COLUMN)
    if stale:
        df.drop(columns=stale, inplace=True)
    if wanted:
        classes = classify_rows(df, rule)
        # A cell with no area is in no class, and cannot be shown to be outside one either: its share value is blank.
        measured = (np.zeros(len(df), dtype=bool) if classes is None else
                    np.isfinite(pd.to_numeric(df[area_column(df.columns)], errors="coerce").to_numpy(dtype=float)))
        for k, col in enumerate(wanted):
            values = np.full(len(df), np.nan)
            if classes is not None:
                values[measured] = (np.asarray(classes)[measured] == k).astype(float)
            df[col] = values
    if chosen is not None:
        k_rule, k = chosen
        df[POPULATION_COLUMN] = population_mask(df, k_rule, k).astype(int)


def apply_population_to_cache(ctx, label: str | None = None) -> None:
    """Project the chosen class onto *label*'s cached rows, or every held well."""
    from review.data.data_loading import get_rows

    chosen, rule = _chosen(ctx), _rule(ctx)
    wells = [label] if label is not None else list(ctx.state.dataset.well_paths or ())
    for well in wells:
        if label is None:
            repo = getattr(ctx.state, "repo", None)
            df = repo.rows_or_none(well) if repo is not None else None
        else:
            df = get_rows(ctx, well)
        if df is None or df.empty:
            continue
        _apply(df, chosen, rule)


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
