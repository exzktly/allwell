"""Which layer removed a cell, and the overlay that says so.

Two defects this file pins, both found by asking the question the Segmentation
tab could not answer.

**The overlay composed three of the six layers.** It ANDed ``Included``,
``QC_Pass`` and ``Crop_Pass`` and knew nothing of ``Gate_Pass``,
``Field_Pass`` or ``Focus_Pass`` — so a cell the focus cut had dropped was
drawn as *included* while every plot, statistic and export left it out. One
picture, two answers, and nothing on screen saying which was right.

**And nothing named the layer.** Inclusion is an AND, so manual curation
cannot restore a cell a verdict column removed — a user clicking such a cell
gets an override written, persisted, and no change on screen. Naming the layer
is what makes that legible.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

from types import SimpleNamespace

import numpy as np
import pandas as pd

from review.features.image_browser.review_image.collect import (
    EXCLUSION_LAYERS,
    review_build_exclusion_reasons,
    review_build_include_map,
)

WELL, FOV, TP = "A01", "1", "0"


def _mask(n_cells=4, size=40):
    """A mask with *n_cells* labels, one per horizontal band."""
    m = np.zeros((size, size), np.int32)
    for k in range(n_cells):
        m[k * 8 + 2: k * 8 + 8, 4:36] = k + 1
    return m


def _ctx(df, overrides=None):
    from review.core.widget_registry import WidgetRegistry

    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(cache={WELL: df}, well_paths={WELL: "x.csv"},
                                data_dir=None, pipeline_info={}))
    ctx = SimpleNamespace(
        state=app._app_state, app=app, widgets=WidgetRegistry(app),
        runtime=SimpleNamespace(review=SimpleNamespace(
            included_overrides=dict(overrides or {}))))
    return ctx


def _frame(**cols):
    n = len(next(iter(cols.values())))
    base = {"nucleus_id": list(range(1, n + 1)),
            "fov": [FOV] * n, "timepoint": [TP] * n}
    base.update(cols)
    return pd.DataFrame(base)


# ── the reason map ──────────────────────────────────────────────────────────

def test_every_layer_the_chokepoint_ands_is_named():
    """The roster must cover the chokepoint, or the overlay goes on being
    blind to whatever it omits — which is exactly how it missed three."""
    import inspect

    from review.data import data_loading

    src = inspect.getsource(data_loading.df_included_mask)
    named = {col for col, _label in EXCLUSION_LAYERS}
    for col in ("Included", "QC_Pass", "Crop_Pass", "Gate_Pass",
                "Field_Pass", "Focus_Pass"):
        assert f'"{col}"' in src, f"{col} is not at the chokepoint any more"
        assert col in named, f"{col} gates inclusion and the overlay cannot name it"


def test_a_focus_excluded_cell_is_named_and_shown_excluded():
    """**The defect.** Excludes the overlay that composes only QC and crop:
    there, this cell is drawn as included while every plot drops it."""
    df = _frame(Included=[1, 1], Focus_Pass=[1, 0])
    ctx = _ctx(df)
    why = review_build_exclusion_reasons(ctx, _mask(2), WELL, FOV, TP)
    assert why[1] == ()
    assert why[2] == ("out of focus",)
    assert review_build_include_map(ctx, _mask(2), WELL, FOV, TP) == {1: True, 2: False}


def test_a_field_excluded_cell_is_named_too():
    """Its sibling layer, which the overlay was equally blind to."""
    df = _frame(Included=[1, 1], Field_Pass=[1, 0])
    why = review_build_exclusion_reasons(_ctx(df), _mask(2), WELL, FOV, TP)
    assert why[2] == ("field flagged by the run",)


def test_a_cell_that_fails_several_layers_names_all_of_them():
    """Inclusion is an AND, so naming only the first would send a user to undo
    something that would not bring the cell back. Excludes the implementation
    that returns a single label."""
    df = _frame(Included=[1], QC_Pass=[0], Focus_Pass=[0])
    why = review_build_exclusion_reasons(_ctx(df), _mask(1), WELL, FOV, TP)
    assert why[1] == ("debris gate", "out of focus")


def test_manual_deletion_is_named_first():
    """It is the one the user did and the only one a click here undoes."""
    df = _frame(Included=[0], QC_Pass=[0])
    why = review_build_exclusion_reasons(_ctx(df), _mask(1), WELL, FOV, TP)
    assert why[1][0] == "deleted here"


def test_a_live_override_beats_the_cached_column():
    """A click takes effect before the next projection, so the override has to
    win here or the overlay lags a redraw behind the user."""
    df = _frame(Included=[1])
    ctx = _ctx(df, overrides={(WELL, FOV, TP, "1"): "0"})
    why = review_build_exclusion_reasons(ctx, _mask(1), WELL, FOV, TP)
    assert why[1] == ("deleted here",)


def test_an_absent_layer_keeps_the_cell():
    """The same fail-open the chokepoint applies: a layer that never ran, or
    ran and was switched off, removes nothing."""
    df = _frame(Included=[1, 1])
    why = review_build_exclusion_reasons(_ctx(df), _mask(2), WELL, FOV, TP)
    assert all(v == () for v in why.values())


def test_an_unreadable_verdict_keeps_the_cell():
    df = _frame(Included=[1, 1], Focus_Pass=[np.nan, "x"])
    why = review_build_exclusion_reasons(_ctx(df), _mask(2), WELL, FOV, TP)
    assert all(v == () for v in why.values())


def test_the_include_map_and_the_reason_map_cannot_disagree():
    """They are one derivation now. Excludes re-composing the AND in two
    places, which is how the overlay came to apply three layers while the
    chokepoint applied six."""
    df = _frame(Included=[1, 0, 1, 1], QC_Pass=[1, 1, 0, 1],
                Focus_Pass=[1, 1, 1, 0])
    ctx = _ctx(df)
    why = review_build_exclusion_reasons(ctx, _mask(4), WELL, FOV, TP)
    inc = review_build_include_map(ctx, _mask(4), WELL, FOV, TP)
    for nid in (1, 2, 3, 4):
        assert inc[nid] == (why[nid] == ()), nid
    assert inc == {1: True, 2: False, 3: False, 4: False}


# ── the overlay ─────────────────────────────────────────────────────────────

def test_every_layer_has_an_ink_and_none_is_the_reserved_tint():
    """A layer with no colour draws nothing, which is a legend entry with no
    mark. ``accent2`` is reserved and forbidden for anomaly use by the plate's
    own invariant; this overlay borrows that rule rather than inventing one."""
    from review.features.image_browser.review_image.renderer import _layer_inks
    from ui import theme

    inks = _layer_inks()
    for _col, label in EXCLUSION_LAYERS:
        assert label in inks, label
    assert theme.Colors.accent2 not in inks.values()


def test_the_toggle_is_off_by_default_and_flips():
    """§7.6: press the real thing. Excludes a toggle wired to nothing, which
    every test asserting the button exists would pass against."""
    import review.features.image_browser.review_image.state as st

    cls = next(v for k, v in vars(st).items()
               if k.endswith("State") and hasattr(v, "show_outline"))
    assert cls().show_exclusion_layers is False


def test_the_toggle_reaches_the_state_and_the_button_text():
    from review.features.image_browser.review_image.renderer import (
        refresh_review_image_layers_btn, toggle_review_image_exclusion_layers,
    )

    seen = {}

    class _Btn:
        def setChecked(self, v): seen["checked"] = v
        def setText(self, t): seen["text"] = t
        def setToolTip(self, t): seen["tip"] = t

    ctx = SimpleNamespace(
        state=SimpleNamespace(review_image=SimpleNamespace(
            show_exclusion_layers=False, preserve_view_on_refresh=False)),
        runtime=SimpleNamespace(review_image=SimpleNamespace(layers_btn=_Btn())))

    import review.features.image_browser.review_image.renderer as rr
    orig = rr.refresh_review_image_ctx
    try:
        rr.refresh_review_image_ctx = lambda _c: None
        toggle_review_image_exclusion_layers(ctx)
    finally:
        rr.refresh_review_image_ctx = orig

    assert ctx.state.review_image.show_exclusion_layers is True
    assert seen["checked"] is True
    assert "On" in seen["text"]
    # The tooltip has to say the thing a user cannot discover by clicking:
    # that a click will not undo a verdict column.
    assert "CANNOT restore" in seen["tip"]
    refresh_review_image_layers_btn(ctx)
