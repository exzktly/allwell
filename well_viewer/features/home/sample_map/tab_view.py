"""Home — **Sample map**: one plate, and a switch saying what it is showing.

The three ways of annotating a well — grouping them, giving them a variable's
value, naming them — were three sibling tabs. This is one tab with three
layers. ``plate_map/controller.py`` holds the switching rule and the reason the
selection travels between two of the three and not the third.

The bodies are the unchanged builders from ``home/groups``, ``home/variables``
and ``home/labels``, each built into its own page of the stack. Nothing about
what a layer *does* moved; what moved is that only one of them is on screen.
"""

from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

import theme
from well_viewer.features.home.sample_map.controller import (
    LAYER_BUILDERS, LAYERS, STACK_KEY, STRIP_KEY, SWITCH_KEY, set_layer,
)


def build_plate_map_subtab(ctx, frame: QWidget) -> None:
    """Build the tab body into *frame*. Registered lazily, like every sub-tab."""
    import importlib

    from widgets.segmented_control import SegmentedControl

    reg = ctx.widgets
    outer = frame.layout() or QVBoxLayout(frame)
    # No margins of its own: ``subtab_inset`` on the descriptor already insets
    # the frame, and a second set here would double it on one tab only.
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(theme.Spacing.sm)

    switch = reg.register(SWITCH_KEY, SegmentedControl(frame))
    for layer in LAYERS:
        switch.addSegment(layer, data=layer)
    outer.addWidget(switch)

    # The role strip is rebuilt per layer rather than three strips being
    # stacked and hidden: the sentence is about what the plate below it means
    # right now, and a hidden sibling is a second answer waiting to be shown by
    # a bug.
    strip_host = reg.register(STRIP_KEY, QWidget(frame))
    strip_column = QVBoxLayout(strip_host)
    strip_column.setContentsMargins(0, 0, 0, 0)
    strip_column.setSpacing(0)
    outer.addWidget(strip_host)

    stack = reg.register(STACK_KEY, QStackedWidget(frame))
    outer.addWidget(stack, 1)
    for layer in LAYERS:
        page = QWidget(stack)
        QVBoxLayout(page).setContentsMargins(0, 0, 0, 0)
        stack.addWidget(page)
        module, builder = LAYER_BUILDERS[layer]
        getattr(importlib.import_module(module), builder)(ctx, page)

    switch.currentChanged.connect(
        lambda index: set_layer(ctx, LAYERS[index] if 0 <= index < len(LAYERS)
                               else LAYERS[0]))
    _wire_selection_publishers(ctx)
    set_layer(ctx, LAYERS[0])


def _wire_selection_publishers(ctx) -> None:
    """Let a click on a layer's own control reach the app-wide selection.

    The two layers whose plate means "the wells I am about to act on" publish
    it; Groups does not, because its plate means a group's membership. Wired
    here rather than inside each body, because it is a fact about the tab —
    those bodies were built for three separate tabs and must not grow a
    dependency on this one.
    """
    from well_viewer.features.home.sample_map.controller import (
        LAYER_PLATES, SHARED_SELECTION_LAYERS, on_layer_selection_changed,
    )

    for layer in SHARED_SELECTION_LAYERS:
        widget = ctx.widgets.get_or_none(LAYER_PLATES.get(layer, ""))
        signal = getattr(widget, "selectionChanged", None)
        if signal is not None:
            signal.connect(
                lambda *_a, name=layer: on_layer_selection_changed(ctx, name))
