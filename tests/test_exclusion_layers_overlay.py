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
    blind to whatever it omits — which is exactly how it missed three.

    **Measured, not scraped.** This read `inspect.getsource(df_included_mask)`
    and looked for each column name as a literal, which stopped describing the
    chokepoint the moment those literals moved into the module-level
    `VERDICT_LAYERS` roster the function now iterates. It went red rather than
    quietly green, which is the survivable direction — but a guard whose
    subject is "what does this function actually exclude" should ask that
    question of the function, not of its text.

    So each layer is driven through `df_included_mask` itself: a row failing
    that column alone must not count. A column the chokepoint silently stopped
    reading fails here; a column the overlay cannot name fails here; and a
    seventh layer added to one and not the other fails here too.
    """
    import pandas as pd

    from review.data.data_loading import VERDICT_LAYERS, df_included_mask

    gating = ("Included",) + tuple(VERDICT_LAYERS)
    named = {col for col, _label in EXCLUSION_LAYERS}
    assert named == set(gating), (
        f"the overlay names {sorted(named)} and inclusion is decided by "
        f"{sorted(gating)}; a cell can be dropped for a reason the picture "
        f"cannot show, or shown a reason that no longer drops it")

    for col in gating:
        df = pd.DataFrame({"nucleus_id": [1, 2]})
        # Every layer all-pass except this one, on row 2 alone.
        for other in gating:
            df[other] = 1
        df[col] = [1, 0]
        mask = list(df_included_mask(df))
        assert mask == [True, False], (
            f"{col} no longer decides inclusion at the chokepoint, so the "
            f"overlay names a layer that does not drop anything: {mask}")


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


# ── what the first draft of the overlay could not say ───────────────────────
#
# Everything below came out of an adversarial review of the shipped feature.
# The theme is one thing: each of these is a property the OLD implementation
# also satisfied under the assertions that existed, which is CLAUDE.md §7.8's
# question -- name the wrong implementation that still passes this -- answered
# out loud. `test_every_layer_has_an_ink_and_none_is_the_reserved_tint` above
# passes with all six inks identical, and two of them were.

_MIN_LAYER_DE = 31.0      #: measured floor on the shipped palette
_MIN_LAYER_LUM = 0.28     #: what a one-pixel stroke needs on this plot surface


def _srgb_to_linear(v):
    v /= 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _rel_luminance(rgb):
    r, g, b = (_srgb_to_linear(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _lab(rgb):
    r, g, b = (_srgb_to_linear(x) for x in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _delta_e(a, b):
    return sum((p - q) ** 2 for p, q in zip(_lab(a), _lab(b))) ** 0.5


def test_no_two_layers_share_a_colour():
    """**The defect.** "Outside the crop" and "outside the scatter gates" were
    both ``text_muted`` — the same pixel value, ΔE exactly 0 — so the overlay
    could not tell apart two of the six answers its own button promises.

    Asserting a *floor* rather than "they differ": two colours can differ by
    one bit and be as unreadable as one. The floor is the palette's measured
    minimum, so it ratchets the way this repository's budgets do.
    """
    from review.features.image_browser.review_image.renderer import (
        _layer_inks, _rgb_of,
    )

    inks = _layer_inks()
    labels = [label for _col, label in EXCLUSION_LAYERS]
    worst, pair = None, None
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            d = _delta_e(_rgb_of(inks[a]), _rgb_of(inks[b]))
            if worst is None or d < worst:
                worst, pair = d, (a, b)
    assert worst >= _MIN_LAYER_DE, (
        f"{pair[0]!r} and {pair[1]!r} are only ΔE {worst:.1f} apart; the "
        f"overlay cannot distinguish two of its own answers")


def test_every_layer_ink_is_bright_enough_to_see():
    """A one-pixel stroke on a dark fluorescence frame. ``text_muted``, which
    two layers used, sits at 0.144 — the two that shared a colour were also
    the two least visible."""
    from review.features.image_browser.review_image.renderer import (
        _layer_inks, _rgb_of,
    )

    inks = _layer_inks()
    for _col, label in EXCLUSION_LAYERS:
        lum = _rel_luminance(_rgb_of(inks[label]))
        assert lum >= _MIN_LAYER_LUM, f"{label!r} is too dark to read ({lum:.3f})"


def test_no_layer_is_painted_in_the_apps_good_colour():
    """Every cell this overlay marks has been **removed**. ``success`` is the
    app's own "good" token — it said the opposite of what happened, and on a
    green-tinted channel a green outline is close to invisible besides."""
    from review.features.image_browser.review_image.renderer import _layer_inks
    from ui import theme

    assert theme.Colors.success not in _layer_inks().values()


def test_the_overlay_is_painted_outside_the_binary_branch():
    """**The defect.** The ink block sat inside the ``else`` of the binary
    split. In Binary mode the reasons were computed — a full row fetch and
    verdict walk per refresh — and then never read, while the button went on
    saying "Why removed: On". A toggle that is on and does nothing.

    **This asserts the structure, and that is the point.** The first version
    of this test drove the shared painter directly and passed against the
    defect, because what was wrong was not the painter but the branch that
    never called it — CLAUDE.md §7.8 exactly: name the wrong implementation
    that still passes. The fix hoists the call out of the ``if``/``else`` so
    no branch can forget it, and what is worth pinning is that it stayed
    hoisted. Anything nested in a conditional is a branch that can be wrong in
    one mode and right in the other, which is how this shipped.
    """
    import ast

    from tests._repo_scan import parse_source, REPO_ROOT

    path = (REPO_ROOT / "review" / "features" / "image_browser" /
            "review_image" / "renderer.py")
    fn = next(n for n in ast.walk(parse_source(path))
              if isinstance(n, ast.FunctionDef) and n.name == "draw_review_image")

    def _calls(body):
        return [n for stmt in body for n in ast.walk(stmt)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "_paint_exclusion_outlines"]

    nested = [stmt for stmt in fn.body if isinstance(stmt, (ast.If, ast.For,
                                                            ast.While, ast.Try))]
    assert not _calls(nested), (
        "the overlay painter is back inside a conditional in "
        "draw_review_image; it must run for every draw mode, and a branch "
        "that can forget it is how Binary mode shipped drawing nothing")
    top = [stmt for stmt in fn.body if not isinstance(stmt, (ast.If, ast.For,
                                                             ast.While, ast.Try))]
    assert len(_calls(top)) == 1, (
        "expected exactly one unconditional call to the overlay painter")


def test_the_overlay_painter_inks_an_excluded_cell():
    """The painter itself, which the structural test above does not measure."""
    import numpy as np
    from review.features.image_browser.review_image.renderer import (
        _layer_inks, _paint_exclusion_outlines, _rgb_of,
    )

    center = np.zeros((6, 6), np.int32)
    center[2:4, 2:4] = 7
    boundary = center > 0
    rgb = np.zeros((6, 6, 3), np.uint8)   # a binary frame: this cell is below
    _paint_exclusion_outlines(rgb, boundary, center,
                              {7: ("out of focus",)}, on=True)
    painted = set(map(tuple, rgb[boundary].tolist()))
    assert painted == {_rgb_of(_layer_inks()["out of focus"])}, painted


def test_the_painter_is_a_no_op_when_the_overlay_is_off():
    import numpy as np
    from review.features.image_browser.review_image.renderer import (
        _paint_exclusion_outlines,
    )

    center = np.zeros((6, 6), np.int32)
    center[2:4, 2:4] = 7
    rgb = np.zeros((6, 6, 3), np.uint8)
    _paint_exclusion_outlines(rgb, center > 0, center,
                              {7: ("out of focus",)}, on=False)
    assert not rgb.any()


def test_the_legend_names_only_the_layers_present_in_this_field():
    """**The defect.** There was no legend anywhere — the button's tooltip
    named the six layers in prose and never said which colour was which, so
    the picture used six inks the user had no way to read.

    Only what is on screen is listed, so every entry answers a cell the user
    can actually see. The Home plate refuses to draw its marker without a
    legend row; this borrows that invariant.
    """
    from review.features.image_browser.review_image.renderer import (
        _exclusion_legend_html, _layer_inks,
    )

    html = _exclusion_legend_html(
        None, {1: ("out of focus",), 2: ("debris gate",)}, on=True)
    assert "out of focus" in html and "debris gate" in html
    assert _layer_inks()["out of focus"] in html
    assert _layer_inks()["debris gate"] in html
    # And nothing about the four layers this field has no cell for.
    for absent in ("outside the crop", "deleted here"):
        assert absent not in html, absent


def test_the_legend_says_so_when_nothing_is_excluded():
    """**The defect.** With the toggle on and nothing excluded the picture is
    unchanged and no surface said why — indistinguishable from a feature that
    does not work (§0.5)."""
    from review.features.image_browser.review_image.renderer import (
        _exclusion_legend_html,
    )

    html = _exclusion_legend_html(None, {}, on=True)
    assert "nothing in this field is excluded" in html


def test_there_is_no_legend_when_the_overlay_is_off():
    from review.features.image_browser.review_image.renderer import (
        _exclusion_legend_html,
    )

    assert _exclusion_legend_html(None, {1: ("out of focus",)}, on=False) == ""


def test_the_hover_readout_names_every_layer_that_dropped_a_cell():
    """**The defect.** Three docstrings and the button's own tooltip promised
    a readout that "names every layer that dropped it", and no such readout
    existed: the hover emitted position, channel value, cell id and MFI and
    nothing about exclusion. A cell failing three layers was drawn in one
    colour with the other two unreachable from any surface in the app.
    """
    from review.features.image_browser.review_image.collect import (
        exclusion_phrase,
    )

    ctx = SimpleNamespace(state=SimpleNamespace(review_image=SimpleNamespace(
        exclusion_reasons_by_nid={3: ("debris gate", "out of focus")})))
    phrase = exclusion_phrase(ctx, 3)
    assert "debris gate" in phrase and "out of focus" in phrase


def test_the_hover_readout_tells_kept_from_unknown():
    """Three states, and collapsing any two of them is the substitution this
    subsystem exists to prevent: the overlay is off (say nothing), it is on
    and this cell is kept (say so), it is on and this cell is out (say what
    dropped it)."""
    from review.features.image_browser.review_image.collect import (
        exclusion_phrase,
    )

    off = SimpleNamespace(state=SimpleNamespace(review_image=SimpleNamespace(
        exclusion_reasons_by_nid=None)))
    assert exclusion_phrase(off, 3) == ""

    on_kept = SimpleNamespace(state=SimpleNamespace(review_image=SimpleNamespace(
        exclusion_reasons_by_nid={3: ()})))
    assert "kept" in exclusion_phrase(on_kept, 3)
