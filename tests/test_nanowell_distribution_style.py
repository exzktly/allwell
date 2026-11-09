"""The Distribution view's **Show** (Auto / Beeswarm / Violin) and the violins' **Smoothing**.

The pure half is checked against the renderer's own artists (dots are hit-map entries, violins are ``nw-density``
collections); the control half drives the real segmented control and slider on a page built by
``build_nanowell_tab`` in the minimal shell, and reads the figure back.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from review.features.nanowell import distribution_compute as dc
from review.features.nanowell import distribution_style_compute as dsc
from review.features.nanowell.state import NanowellState
from tests.test_nanowell_page_compute import _plate


# ── the rule ─────────────────────────────────────────────────────────────────
def test_each_style_draws_violins_past_its_own_well_count():
    assert dsc.density_above("auto") == 4
    assert dsc.density_above("violin") == 0
    assert dsc.density_above("beeswarm") == math.inf
    assert dsc.density_above("nonsense") == 4                   # an unknown style is Auto


@pytest.mark.parametrize("raw, kept", [(150, 150), (10, 25), (900, 300), (149.6, 150), ("2", 100),
                                       (True, 100), (float("nan"), 100), (None, 100)])
def test_smoothing_is_clamped_to_the_slider_s_range(raw, kept):
    assert dsc.clamp_smooth(raw) == kept


def test_smoothing_scales_scott_s_bandwidth_and_100_is_matplotlib_s_own_default():
    from scipy.stats import gaussian_kde

    values = np.random.default_rng(0).normal(0, 1, 200)
    scott = gaussian_kde(values).factor
    assert dsc.bandwidth(100) == "scott"
    assert gaussian_kde(values, bw_method=dsc.bandwidth(250)).factor == pytest.approx(2.5 * scott)
    assert gaussian_kde(values, bw_method=dsc.bandwidth(50)).factor == pytest.approx(0.5 * scott)


def test_the_page_keeps_its_show_and_smoothing_with_its_settings():
    st = NanowellState(dist_style="violin", violin_smooth=180)
    restored = NanowellState()
    restored.apply_config(st.to_config())
    assert (restored.dist_style, restored.violin_smooth) == ("violin", 180)
    restored.apply_config({"dist_style": "pie", "violin_smooth": 5000})
    assert (restored.dist_style, restored.violin_smooth) == ("auto", 300)


def test_batch_export_reads_the_page_s_show_and_smoothing():
    from types import SimpleNamespace

    from review.features.nanowell.controller import distribution_style_of

    st = NanowellState(dist_style="beeswarm", violin_smooth=60)
    ctx = SimpleNamespace(state=SimpleNamespace(feature_slice=lambda name: st if name == "nanowell" else None))
    assert distribution_style_of(ctx) == ("beeswarm", 60)


# ── the figure ───────────────────────────────────────────────────────────────
def _draw(tmp_path, n_wells: int, **kwargs):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from review.features.nanowell.renderer import render_distribution

    wells = [f"A{i:02d}" for i in range(1, n_wells + 1)]
    rng = np.random.default_rng(7)
    per_well = {w: {"t_c": list(rng.integers(0, 9, 12)), "t_a": list(rng.integers(0, 9, 12))} for w in wells}
    metrics, data = _plate(tmp_path, per_well)
    model = dc.distribution_model(metrics, "n", channels=data.channels, groups={"set": wells})
    fig = Figure(figsize=(8, 6.4))
    hits = render_distribution(fig, model, **kwargs)
    violins = [c for c in fig.axes[0].collections if c.get_gid() == "nw-density"]
    return fig, hits, violins


def _footer(fig):
    return [t.get_text() for t in fig.texts]


def test_violin_draws_a_few_wells_as_violins_where_auto_draws_dots(tmp_path):
    _fig, hits, violins = _draw(tmp_path / "auto", 2)
    assert hits.entries and not violins                          # Auto, two wells: dots
    fig, hits, violins = _draw(tmp_path / "violin", 2, style="violin")
    assert not hits.entries and len(violins) == 4                # two wells × two timepoints
    assert any(t.startswith("Show: Violin") for t in _footer(fig))


def test_beeswarm_draws_many_wells_as_dots_where_auto_draws_violins(tmp_path):
    _fig, hits, violins = _draw(tmp_path / "auto", 6)
    assert not hits.entries and violins                          # Auto, six wells: violins
    fig, hits, violins = _draw(tmp_path / "bees", 6, style="beeswarm")
    assert hits.entries and not violins
    assert not any("violin" in t.lower() for t in _footer(fig))


def test_smoothing_changes_the_violin_and_the_figure_says_by_how_much(tmp_path):
    def modes(violin):
        # The bumps along one side of the outline: a narrow kernel follows each cluster of counts, a wide one merges
        # them. (matplotlib clips a violin to its data's range, so the kernel shows in the outline's shape, not its
        # height.)
        v = violin.get_paths()[0].vertices
        x = np.abs(v[:100, 0] - np.median(v[:, 0]))
        return int(((x[1:-1] > x[:-2]) & (x[1:-1] > x[2:])).sum())

    fig_1, _h, default = _draw(tmp_path / "a", 2, style="violin")
    _fig, _h, sharp = _draw(tmp_path / "b", 2, style="violin", smooth=25)
    fig_3, _h, smooth = _draw(tmp_path / "c", 2, style="violin", smooth=300)
    assert len(sharp) == len(default) == len(smooth) == 4
    assert all(modes(s) > modes(d) for s, d in zip(sharp, default))
    assert [modes(v) for v in smooth] == [1, 1, 1, 1]
    assert not any("smoothing" in t for t in _footer(fig_1))
    assert "Violin smoothing 3.00× the automatic (Scott's rule) bandwidth." in _footer(fig_3)


def test_the_window_counts_members_in_violins_by_the_style_drawn():
    from types import SimpleNamespace

    from review.features.nanowell import window_compute as wc

    swarm = SimpleNamespace(well="A01", x=0.0, sites=[("A01", 0, 0)])
    model = SimpleNamespace(axis="wells", panels=[SimpleNamespace(swarms=[swarm])])
    result = SimpleNamespace(rows=[SimpleNamespace(site=("A01", 0, 0))], spans={})
    hits = SimpleNamespace(panels=[], entries=[])
    as_violin = wc.not_drawn(result, model, hits, max_panels=12, density_above=dsc.density_above("violin"))
    as_auto = wc.not_drawn(result, model, hits, max_panels=12, density_above=dsc.density_above("auto"))
    assert as_violin["in densities"] == 1 and as_auto["in densities"] == 0


# ── the controls on the real page ───────────────────────────────────────────
def test_the_show_control_and_the_smoothing_slider_redraw_the_page(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    from review.features.nanowell import controller as nwc
    from tests.test_nanowell_page import _loaded, _page, _texts, _write_nanowell_dataset

    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    st = _loaded(qtbot, ctx)
    fig = ctx.widgets.get("_nw_fig")
    seg = ctx.widgets.get("_nw_dist_style_seg")
    slider = ctx.widgets.get("_nw_violin_smooth_slider")
    assert seg.isVisible() and slider.isVisible()
    assert st.hits.entries and slider.isEnabled()                # Auto, two wells: dots; Smoothing usable

    seg.setCurrentIndex(dsc.STYLE_KEYS.index("violin"))
    assert st.dist_style == "violin"
    assert any(c.get_gid() == "nw-density" for ax in fig.axes for c in ax.collections)
    assert not st.hits.entries

    slider.setValue(200)
    assert st.violin_smooth == 200
    assert ctx.widgets.get("_nw_violin_smooth_label").text() == "2.00×"
    assert "Violin smoothing 2.00× the automatic (Scott's rule) bandwidth." in _texts(ctx, "nw-footer")

    seg.setCurrentIndex(dsc.STYLE_KEYS.index("beeswarm"))
    assert st.hits.entries
    assert not any(c.get_gid() == "nw-density" for ax in fig.axes for c in ax.collections)
    assert not slider.isEnabled()                                # nothing for it to smooth

    nwc.set_view(ctx, "trajectories")                            # a view with no beeswarm: neither control shown
    assert not seg.isVisible() and not slider.isVisible()
