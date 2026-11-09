"""The Plotting band's **Population** control — which size class every cell plot is drawn from.

Cell Gating's size classes (``review.domain.size_classes``) name two populations told apart by area. Choosing one
here restricts the whole analysis to it (``QCParams.population``, projected as ``Population_Pass`` by
``review.data.population_gate``), so every cell plot, statistic, threshold count, fold change and export is of that
class alone, and every cell figure says so in its footer (:func:`population_line`, written by
``observation_band.stamp_cells_footer``). *All cells* lifts it.

When it is offered is decided here, never tab by tab: the control is shown while the rule is complete and the leaf
draws cells (``observation_band.effective_observations``); a choice is applied through the QC layer's service
(``qc.set_population``), so a build without the QC layer shows nothing. A saved choice whose rule has since become
incomplete restricts nothing (``QCParams.population_rule``), and the control is then hidden rather than showing a
class that no longer exists. Core: imports no feature.
"""

from __future__ import annotations

#: What the control reads for every cell.
ALL_CELLS = "All cells"


def _params(ctx):
    features = getattr(ctx, "features", None)
    return features.query("qc.get_params", ctx, default=None) if features is not None else None


def offered_labels(ctx) -> tuple[str, ...]:
    """The class labels the control offers — those of a complete size-class rule — or ``()``."""
    rule = getattr(_params(ctx), "size_classes", None)
    active = getattr(rule, "active", None)
    return tuple(rule.labels) if callable(active) and active() else ()


def population_line(ctx) -> str:
    """The footer clause naming the class every cell figure is drawn from — ``Population: T cells only (80–250 px;
    DCs left out)`` — or ``""`` while every cell counts."""
    params = _params(ctx)
    chosen = params.population_rule() if params is not None else None
    if chosen is None:
        return ""
    rule, k = chosen
    others = [label for i, label in enumerate(rule.labels) if i != k]
    line = (f"Population: {rule.labels[k]} only ({rule.classes[k].range_text()}; "
            f"{', '.join(others)} and cells in neither class left out)")
    from review.data.population_gate import unclassable_wells

    wells = getattr(getattr(ctx.state, "selection", None), "selected_wells", None) or ()
    missing = unclassable_wells(ctx, sorted(wells))
    if missing:
        line += (f" · {len(missing)} well(s) have no cell-area column, so none of their cells could be classed "
                 f"and none is shown: {', '.join(missing[:6])}{' …' if len(missing) > 6 else ''}")
    return line


def build_population_controls(ctx, band) -> None:
    """Add the Population picker to *band*'s scope zone, hidden until :func:`refresh` shows it."""
    from widgets.labeled_combo import LabeledComboBox

    combo = ctx.widgets.register("_plotting_population_cb", LabeledComboBox("Population"))
    combo.setToolTip("Draw every cell plot from one of the size classes set on Home ▸ Cell Gating, or from all "
                     "cells. Every plot, statistic, threshold count, fold change and export follows, and each figure "
                     "names the population in its footer.")

    def _on_change(_idx: int) -> None:
        data = combo.currentData()
        choice = None if data is None or data < 0 else int(data)
        params = _params(ctx)
        if params is None or params.population == choice:
            return
        ctx.features.call("qc.set_population", ctx, choice)

    combo.currentIndexChanged.connect(_on_change)
    group = band.add_scope("", combo)
    group.setVisible(False)
    ctx.widgets.register("_plotting_population_grp", group)


def refresh(ctx, title: str | None = None) -> None:
    """Fill the picker with the rule's labels and the choice in force; show it on a leaf that draws cells while the
    rule is complete. Never raises: the band is chrome (the failure is logged)."""
    from core.safe import swallow

    with swallow("show the Population picker"):
        _refresh(ctx, title)


def _refresh(ctx, title: str | None) -> None:
    combo = ctx.widgets.get_or_none("_plotting_population_cb")
    group = ctx.widgets.get_or_none("_plotting_population_grp")
    if combo is None or group is None:
        return
    if title is None:
        stack = ctx.widgets.get_or_none("_plotting_notebook")
        title = stack.currentName() if stack is not None else ""
    labels = offered_labels(ctx)
    from review.core.observation_band import effective_observations
    from review.domain.observation import CELLS

    group.setVisible(bool(labels) and bool(title) and effective_observations(ctx, title) == CELLS)
    params = _params(ctx)
    chosen = params.population if params is not None and params.population_rule() is not None else None
    blocked = combo.blockSignals(True)
    try:
        combo.clear()
        combo.addItem(ALL_CELLS, -1)
        for k, label in enumerate(labels):
            combo.addItem(f"{label} only", k)
        combo.setCurrentIndex(0 if chosen is None else chosen + 1)
    finally:
        combo.blockSignals(blocked)
