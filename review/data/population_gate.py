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

The same projector writes the **class columns** while the rule is complete, whether or not a class is chosen
(``size_classes.class_columns``):

* each class's **share column** (``shape_class_share::<label>``: 1 in the class, 0 outside it, blank with no area),
  whose well mean is that class's share of the well's kept cells;
* the **class ratio column** (``shape_class_ratio::<label 1>::<label 2>``: 1 in the first class, 0 in the second, blank
  otherwise), which every reduction counts as n(first) / n(second) rather than averaging
  (``size_classes.class_ratio_of_counts``, ``data_loading._ratio_timepoint``).

So every plot that takes a per-cell value offers each class's share and the classes' ratio — and their fold changes
— beside the shape features. A class column of a class since renamed or removed is dropped, so the rows never carry
one the rule no longer defines. Under a one-class population the columns are still written but not offered
(``channel_state_controller._class_channels``): with one class out of every aggregate they have nothing to say.

Not to be confused with the per-nanowell class *counts*, which must see both classes and so read the inclusion with
this layer left out (``nanowell.controller.gate_tally``).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

#: The verdict column (``data_loading.VERDICT_LAYERS``).
POPULATION_COLUMN = "Population_Pass"

_logger = logging.getLogger("review")


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
    """The area columns a class is judged on, while the rule is complete (the class columns are written then, and
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


def _plan(df: pd.DataFrame, chosen, rule=None) -> tuple[list[str], dict[str, np.ndarray]]:
    """What :func:`_apply` writes onto *df*, computed without touching it: ``(columns to drop, {column: values})``.

    ``Population_Pass`` for *chosen* (``(rule, k)`` or ``None``) and the share and ratio columns of *rule* (a complete
    rule or ``None``); a class column the rule no longer defines, and ``Population_Pass`` with no class chosen, are
    dropped. Split from the write (:func:`_commit`) so the write is the one step that changes the frame."""
    from review.domain.size_classes import (
        area_column, class_columns, classify_rows, is_class_column, class_ratio_column, share_columns,
    )

    shares = share_columns(rule) if rule is not None else []
    ratio = class_ratio_column(rule) if rule is not None else None
    wanted = class_columns(rule) if rule is not None else []
    stale = [c for c in df.columns if is_class_column(c) and c not in wanted]
    if chosen is None and POPULATION_COLUMN in df.columns:
        stale.append(POPULATION_COLUMN)
    columns: dict[str, np.ndarray] = {}
    if wanted:
        classes = classify_rows(df, rule)
        # A cell with no area is in no class, and cannot be shown to be outside one either: its share value is blank.
        measured = (np.zeros(len(df), dtype=bool) if classes is None else
                    np.isfinite(pd.to_numeric(df[area_column(df.columns)], errors="coerce").to_numpy(dtype=float)))
        for k, col in enumerate(shares):
            values = np.full(len(df), np.nan)
            if classes is not None:
                values[measured] = (np.asarray(classes)[measured] == k).astype(float)
            columns[col] = values
        if ratio:
            # 1 in the first class, 0 in the second, blank for any other cell: a reduction counts the 1s over the
            # 0s (``size_classes.class_ratio_of_counts``), so a cell in neither class is in neither side of the ratio.
            values = np.full(len(df), np.nan)
            if classes is not None:
                kinds = np.asarray(classes)
                values[kinds == 0] = 1.0
                values[kinds == 1] = 0.0
            columns[ratio] = values
    if chosen is not None:
        k_rule, k = chosen
        columns[POPULATION_COLUMN] = population_mask(df, k_rule, k).astype(int)
    return stale, columns


def _commit(df: pd.DataFrame, plan: tuple[list[str], dict[str, np.ndarray]]) -> None:
    """Write a :func:`_plan` onto *df*."""
    stale, columns = plan
    if stale:
        df.drop(columns=stale, inplace=True)
    for col, values in columns.items():
        df[col] = values


def _apply(df: pd.DataFrame, chosen, rule=None) -> None:
    """Write ``Population_Pass`` for *chosen* (``(rule, k)`` or ``None``) and the share and ratio columns of *rule* (a
    complete rule or ``None``) onto *df*, dropping whichever does not apply."""
    _commit(df, _plan(df, chosen, rule))


def apply_population_to_cache(ctx, label: str | None = None) -> list[str]:
    """Project the chosen class onto *label*'s cached rows, or every held well.

    **Every held well ends up under one rule.** The class columns are named by the classes' labels, not their
    bounds, so a well left with the previous rule's values looks exactly like one rewritten under the new rule — and
    a walk that ran out of memory part-way used to leave exactly that: the wells before the failure under the new
    bounds, the wells after it under the old, the panel showing the new, and nothing on any plot to say so.

    The walk still goes one well at a time, so it never holds more than one well's new columns (computing every
    well's first would add the whole plate's to the peak, on the very machines that run short). If it fails, the
    held wells are dropped from the row cache (:func:`_release`), freeing their memory, and each comes back through
    ``data_loading.load_and_project``, which runs this projector on that one well under the rule now in force, the
    next time a view reads it. Every well therefore answers under one rule, at the cost of re-reading those wells. The dropped wells are returned, and left on
    ``DatasetState.released_wells`` for the window's thread to report (``notifications.report_released_wells``) —
    this can run on a worker. Any failure other than running out of memory is raised after the release, so a defect
    is not mistaken for a full machine. Returns ``[]`` when every held well was rewritten."""
    from review.data.data_loading import get_rows

    chosen, rule = _chosen(ctx), _rule(ctx)
    if label is not None:
        df = get_rows(ctx, label)
        if df is not None and not df.empty:
            _apply(df, chosen, rule)
        return []
    repo = getattr(ctx.state, "repo", None)
    held = []
    for well in list(ctx.state.dataset.well_paths or ()):
        df = repo.rows_or_none(well) if repo is not None else None
        if df is not None and not df.empty:
            held.append((well, df))
    try:
        for _well, df in held:
            _apply(df, chosen, rule)
    except Exception as exc:
        wells = [well for well, _df in held]
        # This walk's own references would keep every frame alive through the release, freeing nothing.
        held = df = None
        _release(ctx, wells, exc, chosen, rule)
        if not isinstance(exc, MemoryError):
            raise
        return wells
    return []


def _release(ctx, wells: list[str], exc: BaseException, chosen, rule) -> None:
    """Drop the held frames after a failed rewrite, so each is read back under the rule now in force.

    ``RowCache.release`` keeps a pinned frame — one ``load_and_project`` is working on, on another thread — and the
    failure may have been part-way through that very frame. So each frame still held afterwards is written again
    under the rule in force, one well's columns at a time; if even that fails it is dropped as well."""
    dataset = ctx.state.dataset
    cache = dataset.cache
    release = getattr(cache, "release", None)
    if release is not None:
        release()
    else:
        cache.clear()
    for well in wells:
        try:
            df = cache.get(well)
        except KeyError:
            df = None
        if df is None or df.empty:
            continue
        try:
            _apply(df, chosen, rule)
        except Exception:
            _logger.exception("Size classes: could not rewrite well %s, held while the others were dropped", well)
            cache.pop(well, None)
    # A frame read back differs from the one dropped (the new rule against a half-applied one), so nothing
    # remembered of the old frames may be served — unlike an ordinary eviction, which brings back the same rows.
    note = getattr(dataset, "note_rows_changed", None)
    if callable(note):
        note()
    released = getattr(dataset, "released_wells", None)
    if isinstance(released, list):
        released.extend(w for w in wells if w not in released)
    _logger.warning("Size classes: rewriting the class columns of %d held well(s) failed (%s: %s); they were dropped "
                    "from memory and are read back under the size classes now in force when next drawn",
                    len(wells), type(exc).__name__, exc)


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
