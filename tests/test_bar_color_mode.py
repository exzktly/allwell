"""Properties → Colours: plate-rank colours, or one grey for every bar.

Three separate things have to hold, and the first two are the ones that have
shipped broken here before:

* The assertion sits at the **boundary** (§7.4 #1) — what colour reached the
  drawn patch, not what colour a helper computed. A test that reads the derived
  value passes happily while the screen stays wrong, which is exactly what the
  existing dial tests did.
* The **shortfall marks keep their own colours**. Grey mode restyles data ink.
  A placeholder column and a real one going the same grey would make a
  measurement that does not exist look like one that does — §7's cardinal sin,
  reached by a styling change rather than by a bug.
* Both surfaces that draw these panels — the Bar tab and the Batch Export bar
  figures — answer the question the same way (§7.5 #5).

The control itself is pressed in ``test_properties_colour_control.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

import theme
from well_viewer.core.plot_state import PlotState
from well_viewer.domain.bar_colors import (
    BAR_COLOR_MODES,
    apply_bar_color_mode,
    bar_color_mode,
    mono_bar_color,
)
from well_viewer.features.barplot.collect import BarItem
from well_viewer.features.barplot.controller import render_bar_items

_PLATE_A, _PLATE_B = "#6AB2FB", "#D406F9"
_PLACEHOLDER = "#101010"


# ── the resolver ────────────────────────────────────────────────────────────
def test_the_default_mode_is_the_colours_the_app_has_always_drawn():
    assert PlotState().bar_color_mode == "plate"
    assert mono_bar_color(PlotState()) is None
    assert apply_bar_color_mode(PlotState(), [_PLATE_A, _PLATE_B]) == [_PLATE_A, _PLATE_B]


def test_grey_mode_collapses_every_column_to_one_ink():
    plot = PlotState()
    plot.bar_color_mode = "grey"
    assert mono_bar_color(plot) == theme.Colors.plot_mono
    assert apply_bar_color_mode(plot, [_PLATE_A, _PLATE_B]) == [
        theme.Colors.plot_mono, theme.Colors.plot_mono]


def test_an_unrecognised_mode_reads_as_plate_rather_than_as_blank():
    """A hand-edited persistence file, or a state object from an older build.
    Degrading to the historical colours is recoverable; degrading to an unknown
    colour string is a matplotlib error at draw time."""
    plot = PlotState()
    plot.bar_color_mode = "chartreuse"
    assert bar_color_mode(plot) == "plate"
    assert mono_bar_color(plot) is None


def test_the_mode_list_and_the_defaults_agree():
    assert PlotState().bar_color_mode in BAR_COLOR_MODES
    assert set(BAR_COLOR_MODES) == {"plate", "grey"}


def test_the_returned_list_is_a_copy_of_the_same_length():
    """Callers zip it against the well / label sequences it was built beside."""
    src = [_PLATE_A, _PLATE_B]
    out = apply_bar_color_mode(PlotState(), src)
    assert out == src and out is not src
    plot = PlotState()
    plot.bar_color_mode = "grey"
    assert len(apply_bar_color_mode(plot, src)) == len(src)


def test_the_mono_ink_is_a_theme_token_not_a_literal():
    """ARCHITECTURE §8 — and the practical half of it: an inline grey here
    would be invisible to a palette change and to the contrast checks."""
    plot = PlotState()
    plot.bar_color_mode = "grey"
    assert mono_bar_color(plot) == theme.Colors.plot_mono
    assert to_hex(theme.Colors.plot_mono)


# ── the grey has to read on two very different grounds ──────────────────────
#: The alpha ``render_bar_items`` and the per-cell renderers draw bar fills at.
#: The composited pixel is what a reader sees, so it is what gets measured —
#: the raw token clears thresholds the drawn bar does not.
_BAR_ALPHA = 0.85
#: WCAG's contrast floor for non-text. A bar is a solid shape, not type.
_NON_TEXT_FLOOR = 3.0


def _relative_luminance(rgb) -> float:
    def _channel(value: float) -> float:
        v = value / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _rgb(hex_colour: str):
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _contrast(a, b) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _over(colour: str, ground: str, alpha: float = _BAR_ALPHA):
    c, g = _rgb(colour), _rgb(ground)
    return tuple(alpha * c[i] + (1 - alpha) * g[i] for i in range(3))


@pytest.mark.parametrize("ground_name,ground", [
    ("publication white (CPub.bg)", theme.CPub.bg),
    ("dark card fill (Colors.plot_bg)", theme.Colors.plot_bg),
])
def test_the_mono_ink_reads_against_both_grounds_once_the_alpha_is_applied(
        ground_name, ground):
    """One token has to serve a white page and a near-black card, and the bars
    are drawn at 0.85 alpha — so the number that matters is the *composited*
    one, not the token's.

    The first grey chosen here (#8A9099) measured 3.22:1 raw and **2.61:1**
    drawn on white, under a comment asserting it read on both grounds. That is
    the §7.5 #7 failure: a property stated without measuring it. This test is
    the measurement, kept beside the claim.
    """
    drawn = _over(theme.Colors.plot_mono, ground)
    ratio = _contrast(drawn, _rgb(ground))
    assert ratio >= _NON_TEXT_FLOOR, (
        f"{theme.Colors.plot_mono} at alpha {_BAR_ALPHA} draws as "
        f"{tuple(round(c) for c in drawn)} on {ground_name} ({ground}) — "
        f"contrast {ratio:.2f}:1, below the {_NON_TEXT_FLOOR}:1 non-text floor. "
        "Publication is the ground this mode mostly exists for; moving the "
        "token trades one ground against the other, so check both."
    )


def test_the_mono_ink_is_not_overridden_by_any_theme_bundle():
    """The single-token design rests on bar fill being theme-independent, the
    way ``WELL_COLORS_TUPLE`` is. A bundle overriding it would silently restore
    the screen/publication divergence the token exists to avoid — and would
    also move it out from under the contrast test above."""
    offenders = [
        f"{name}.{cls_key}"
        for name, bundle in theme.THEMES.items()
        for cls_key, attrs in bundle.items()
        if "plot_mono" in attrs
    ]
    assert not offenders, (
        "theme bundle(s) override Colors.plot_mono: " + ", ".join(offenders))


# ── the boundary: the colour of the patch that was actually drawn ───────────
def _items() -> list[BarItem]:
    """One measured column and one whose value could not be computed, so both
    the data ink and the shortfall placeholder are exercised in one pass."""
    return [
        BarItem(key="A01", display="A01", color=_PLATE_A,
                mean=5.0, spread=0.5, has_mean=True,
                frac=0.4, frac_spread=0.05, has_frac=True,
                n_above=12.0, n_above_spread=1.0, tp=0.0, group_key=""),
        BarItem(key="A02", display="A02", color=_PLATE_B,
                mean=float("nan"), spread=0.0, has_mean=False,
                frac=float("nan"), frac_spread=0.0, has_frac=False,
                n_above=float("nan"), n_above_spread=0.0, tp=0.0, group_key=""),
    ]


def _render(bar_color):
    """Render onto real axes and return the three panels' drawn bar colours.

    Real ``Axes``, not a spy: ``render_bar_items`` autoscales, sets locators
    and writes tick labels through them, and a stub that answers those calls
    with ``None`` is a harness that can only ever confirm itself (§7.3 #1)."""
    fig = Figure(figsize=(6, 8))
    ax_mean = fig.add_subplot(3, 1, 1)
    ax_frac = fig.add_subplot(3, 1, 2)
    ax_n = fig.add_subplot(3, 1, 3)
    render_bar_items(
        ax_mean=ax_mean, ax_frac=ax_frac, ax_n=ax_n,
        use_groups=False, items=_items(), threshold=1.0,
        warn_color="#F59E0B", border_color="#333333",
        placeholder_color=_PLACEHOLDER, disabled_well_color="#404040",
        err_bar_color="#4A5568", bar_color=bar_color,
    )
    return tuple(
        [to_hex(p.get_facecolor()).upper() for p in ax.patches]
        for ax in (ax_mean, ax_frac, ax_n)
    )


def test_without_the_mode_each_bar_keeps_its_own_plate_colour():
    means, fracs, ns = _render(None)
    for panel, drawn in (("mean", means), ("frac", fracs), ("n", ns)):
        assert drawn == [_PLATE_A.upper(), _PLACEHOLDER.upper()], f"{panel} panel"


def test_grey_mode_reaches_every_panel_not_only_the_mean():
    """Mean, Fraction and Events are three separate ``ax.bar`` calls in the
    same loop. A fix applied to the first would leave a grey Mean panel above
    two plate-coloured ones."""
    grey = theme.Colors.plot_mono.upper()
    means, fracs, ns = _render(theme.Colors.plot_mono)
    for panel, drawn in (("mean", means), ("frac", fracs), ("n", ns)):
        assert drawn[0] == grey, f"{panel} panel drew {drawn[0]!r}"


def test_the_missing_value_placeholder_is_not_greyed_with_the_data():
    """The dashed placeholder says "this was never computed". Painting it the
    same grey as a real bar makes an absent measurement indistinguishable from
    a present one."""
    grey = theme.Colors.plot_mono.upper()
    means, fracs, ns = _render(theme.Colors.plot_mono)
    for panel, drawn in (("mean", means), ("frac", fracs), ("n", ns)):
        assert drawn == [grey, _PLACEHOLDER.upper()], f"{panel} panel"


def test_the_batch_export_bar_figure_resolves_the_mode_from_the_same_state():
    """§7.5 #5: two surfaces answering one question must not answer it
    differently. The batch panel renders with ``ctx=None`` on purpose, so it
    resolves the mode itself — this pins that it resolves it from the same
    field the tab does, through the same helper."""
    from well_viewer.features.batch_export.bar_panel import _mono_bar_color

    plot = PlotState()
    assert _mono_bar_color(plot) is None
    plot.bar_color_mode = "grey"
    assert _mono_bar_color(plot) == theme.Colors.plot_mono


# ── persistence ─────────────────────────────────────────────────────────────
def _persist_ctx(plot):
    return SimpleNamespace(
        state=SimpleNamespace(
            plot=plot,
            bar=SimpleNamespace(tp_selections={}),
            feature_slice=lambda _n: None,
        ),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
    )


@pytest.mark.parametrize("mode", BAR_COLOR_MODES)
def test_the_mode_survives_a_save_and_reload(mode):
    from well_viewer.persistence import view_state as vs

    plot = PlotState()
    plot.bar_color_mode = mode
    snap = vs._snapshot_bar(_persist_ctx(plot))
    assert snap["bar_color_mode"] == mode

    restored = PlotState()
    vs._apply_bar(_persist_ctx(restored), snap)
    assert restored.bar_color_mode == mode


def test_a_junk_value_on_disk_does_not_reach_the_state():
    from well_viewer.persistence import view_state as vs

    plot = PlotState()
    vs._apply_bar(_persist_ctx(plot), {"bar_color_mode": "chartreuse"})
    assert plot.bar_color_mode == "plate"
