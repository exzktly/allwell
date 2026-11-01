"""The Plotting band's **Observations: Cells | Nanowells** toggle — what a plot's points are.

Shown first in the band, because it changes what every other control lists. When it is offered is decided, never
written down tab by tab (CLAUDE.md §3):

* a **standard plate** shows nothing — there is nothing else to choose;
* a **nanowell dataset in a build with the Nanowells feature**, on a leaf whose registry descriptor declares
  ``observations`` holding ``"nanowells"``, shows the live toggle;
* the same dataset on a leaf that declares cells only shows the static text "Observations: cells — no nanowell view
  for this plot", so a control that cannot apply is not offered and its absence is explained;
* a nanowell dataset in a build **without** the Nanowells feature shows nothing; its cell plots say in their footer
  that the nanowell views are not in this build (``observation.pooled_cells_line``).

The choice is ``ctx.state.plot.observations``; :func:`effective_observations` is what a leaf draws — cells on a leaf
that cannot draw nanowells, whatever the toggle last said on another leaf. Core: imports no feature.
"""

from __future__ import annotations

from review.domain.observation import CELLS, NANOWELLS, OBSERVATION_LABELS

#: The static text on a nanowell dataset's leaf that draws cells only.
CELLS_ONLY_TEXT = "Observations: cells — no nanowell view for this plot"


def leaf_observations(ctx, title: str) -> tuple[str, ...]:
    """What the Plotting leaf *title* declares it can draw (its registry descriptor), cells when undeclared."""
    features = getattr(ctx, "features", None)
    descriptor = features.get(title) if features is not None else None
    return tuple(getattr(descriptor, "observations", (CELLS,)) or (CELLS,))


def effective_observations(ctx, title: str) -> str:
    """What the leaf *title* draws now: the toggle's choice when this dataset, build and leaf all allow it."""
    from review.data.observations import available_observations

    chosen = getattr(ctx.state.plot, "observations", CELLS)
    if chosen == NANOWELLS and NANOWELLS in available_observations(ctx) and NANOWELLS in leaf_observations(ctx, title):
        return NANOWELLS
    return CELLS


def band_mode(ctx, title: str) -> str:
    """``"hidden"``, ``"toggle"`` or ``"cells_only"`` — what the band shows for the leaf *title*."""
    from review.data.observations import available_observations

    if NANOWELLS not in available_observations(ctx):
        return "hidden"
    return "toggle" if NANOWELLS in leaf_observations(ctx, title) else "cells_only"


def build_observation_controls(ctx, band) -> None:
    """Add the toggle and its static stand-in to *band*'s scope zone, hidden until :func:`refresh` shows one.

    Called first when the band is built, so the group sits at the start of the row."""
    from PySide6.QtWidgets import QLabel

    from widgets.segmented_control import SegmentedControl

    toggle = SegmentedControl()
    for key in (CELLS, NANOWELLS):
        toggle.addSegment(OBSERVATION_LABELS[key], data=key)

    def _on_change(_idx: int) -> None:
        set_observations(ctx, toggle.currentData())

    toggle.currentChanged.connect(_on_change)
    ctx.widgets.register("_plotting_observations_toggle", toggle)
    group = band.add_scope("Observations", toggle)
    group.setVisible(False)
    ctx.widgets.register("_plotting_observations_grp", group)
    static = QLabel(CELLS_ONLY_TEXT)
    static_group = band.add_scope("", static)
    static_group.setVisible(False)
    ctx.widgets.register("_plotting_observations_static", static_group)


def set_observations(ctx, observations: str) -> None:
    """Record the choice and redraw every plot, so each leaf draws what it now means."""
    if observations not in (CELLS, NANOWELLS) or getattr(ctx.state.plot, "observations", CELLS) == observations:
        return
    ctx.state.plot.observations = observations
    from review.core.plot_orchestrator import redraw_every_scope

    redraw_every_scope(ctx)


def refresh(ctx, title: str) -> None:
    """Show the toggle, its static text, or neither, for the Plotting leaf *title*; mirror the recorded choice."""
    mode = band_mode(ctx, title)
    group = ctx.widgets.get_or_none("_plotting_observations_grp")
    static = ctx.widgets.get_or_none("_plotting_observations_static")
    toggle = ctx.widgets.get_or_none("_plotting_observations_toggle")
    if group is not None:
        group.setVisible(mode == "toggle")
    if static is not None:
        static.setVisible(mode == "cells_only")
    if toggle is not None:
        blocked = toggle.blockSignals(True)
        try:
            toggle.setCurrentByData(getattr(ctx.state.plot, "observations", CELLS))
        finally:
            toggle.blockSignals(blocked)


def refresh_current(ctx) -> None:
    """:func:`refresh` for whichever Plotting leaf is current — a dataset load changes what may be offered."""
    stack = ctx.widgets.get_or_none("_plotting_notebook")
    title = stack.currentName() if stack is not None else ""
    if title:
        refresh(ctx, title)
