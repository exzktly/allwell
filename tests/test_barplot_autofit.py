"""Bar-plot y-axis auto-fit: keep the 0 baseline, trim the top to the tallest
bar + its error bar (all panels; Fraction capped at 1.05)."""
from __future__ import annotations

import pytest

pytest.importorskip("matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from review.features.barplot.collect import BarItem
from review.features.barplot.controller import render_bar_items


def _item(key, mean, spread, frac, frac_spread, n_above, n_spread):
    return BarItem(
        key=key, display=key, color="#888888",
        mean=mean, spread=spread, has_mean=True,
        frac=frac, frac_spread=frac_spread, has_frac=True,
        n_above=n_above, n_above_spread=n_spread,
    )


def _render(items):
    fig, (axm, axf, axn) = plt.subplots(1, 3)
    render_bar_items(
        ax_mean=axm, ax_frac=axf, use_groups=False, items=items,
        threshold=0.5, warn_color="#ffaa00", border_color="#888888",
        placeholder_color="#444444", disabled_well_color="#666666",
        err_bar_color="#ff0000", ax_n=axn,
    )
    out = (axm.get_ylim(), axf.get_ylim(), axn.get_ylim())
    plt.close(fig)
    return out


def test_autofit_keeps_zero_baseline_and_trims_top():
    items = [_item("A01", 100.0, 10.0, 0.30, 0.05, 8.0, 1.0),
             _item("B02", 200.0, 20.0, 0.20, 0.02, 5.0, 0.0)]
    (m_lo, m_hi), (f_lo, f_hi), (n_lo, n_hi) = _render(items)
    # honest 0 baseline on every panel
    assert m_lo == 0.0 and f_lo == 0.0 and n_lo == 0.0
    # mean top hugs the tallest bar + error (200+20=220) with a little headroom
    assert 220.0 <= m_hi <= 220.0 * 1.2
    # fraction fits the data (max 0.30+0.05=0.35) — far below the old fixed 1.05
    assert 0.35 <= f_hi <= 0.5
    # N fits the tallest (8+1=9) with headroom
    assert 9.0 <= n_hi <= 9.0 * 1.2


def test_autofit_fraction_capped_at_1_05():
    items = [_item("A01", 1.0, 0.0, 0.98, 0.10, 1.0, 0.0)]
    _, (f_lo, f_hi), _ = _render(items)
    assert f_lo == 0.0
    assert f_hi <= 1.05  # capped, not 0.98+0.10=1.08 → *1.08


def test_autofit_baseline_dips_below_zero_for_negative_means():
    # fold-change can go negative → the baseline must drop below 0 to show it.
    items = [_item("A01", -0.5, 0.1, 0.2, 0.0, 0.0, 0.0),
             _item("B02", 2.0, 0.2, 0.3, 0.0, 0.0, 0.0)]
    (m_lo, m_hi), _, _ = _render(items)
    assert m_lo <= -0.6      # mean-spread of the negative bar (-0.5-0.1)
    assert m_hi >= 2.2       # tallest positive bar + error
