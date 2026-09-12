"""The ON cutoff is drawn on the CDFs, and moves when the user moves it.

The Cell Gating CDFs are the surface for choosing ThreshFracOn — the optimiser
directly above them searches for it — and it was the one line not on them. Only
the FluorGating floor was drawn, so a user setting the ON cutoff had no way to
see where it fell on the distribution, and the two numbers are constantly
confused for each other precisely because one was visible and the other was not.

The redraw was the other half. ``cell_gating_on_gating_change`` clears the axes
stack and replots for its FluorGating line; its ThreshFracOn sibling, ten lines
of code away, did not — so the cutoff the user had just typed stayed drawn
where the old one was, on the panel they were looking at to choose it.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace

import pytest

pytest.importorskip("matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")

from review.features.home.cell_gating.renderer import (  # noqa: E402
    _effective_thresh_frac_on,
)


def _ctx(*, stored=None, seg=""):
    return SimpleNamespace(state=SimpleNamespace(
        cell_gating=SimpleNamespace(thresh_frac_on=dict(stored or {})),
        channel=SimpleNamespace(seg_channel_token=seg),
    ))


def test_a_stored_cutoff_is_what_gets_drawn():
    assert _effective_thresh_frac_on(_ctx(stored={"gfp": 72.5}), "gfp") == 72.5


def test_an_unset_channel_falls_back_to_its_default():
    from review.domain.gating_state import DEFAULT_THRESH_FRAC_ON

    assert _effective_thresh_frac_on(_ctx(), "gfp") == DEFAULT_THRESH_FRAC_ON


def test_the_nuclear_channel_gets_its_own_default_not_the_readout_one():
    """Its default is 0 — "no cut" — so no line is drawn for it."""
    assert _effective_thresh_frac_on(_ctx(seg="dapi"), "dapi") is None


def test_a_cutoff_of_zero_draws_no_line():
    """Zero means "no cut"; a line on the axis edge claiming to be a threshold
    is worse than no line."""
    assert _effective_thresh_frac_on(_ctx(stored={"gfp": 0.0}), "gfp") is None


def test_the_channel_key_is_matched_case_insensitively():
    assert _effective_thresh_frac_on(_ctx(stored={"gfp": 60.0}), "GFP") == 60.0


def test_the_renderer_draws_the_cutoff_line():
    from review.features.home.cell_gating import renderer

    src = textwrap.dedent(inspect.getsource(renderer._cell_gating_plot_cdf))
    assert "_effective_thresh_frac_on" in src, (
        "the CDF panel is back to drawing only the FluorGating floor"
    )


def test_editing_the_cutoff_redraws_the_cdfs():
    from review.features.home.cell_gating import controller

    src = textwrap.dedent(inspect.getsource(
        controller.cell_gating_on_threshold_frac_on_change))
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_cell_gating_plot_cdf"]
    assert calls, (
        "a ThreshFracOn edit no longer redraws the CDFs it is chosen on, while "
        "its FluorGating sibling does"
    )
