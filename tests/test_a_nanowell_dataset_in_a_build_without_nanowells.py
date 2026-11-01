"""A nanowell dataset in a build without the Nanowells feature, and in one with it (NANOWELL_OBSERVATIONS_TRACKER
#20, #2; amendment B).

Removing the Nanowells feature removes nanowell plotting with it: no Observations toggle, no nanowell overlays, and
cell plots that say the nanowell views are not in this build. The app must still boot, load a nanowell dataset and
operate with the package blocked at import — ``test_feature_removal`` proves that only on a standard plate, which
never reaches a nanowell code path. The inverse case checks the same surfaces with the feature present, opening
``out/`` directly so the real loader's ``in/`` resolution is exercised too.

Full-app constructions: two, slow lane.
"""

from __future__ import annotations

import sys

import pytest

from tests.test_feature_removal import _ImportBlocker, _exercise, _prune_registry


def _build(qtbot, rt, path):
    app = rt.ReviewApp()
    qtbot.addWidget(app)
    app._load_path(path)
    return app


def _bar_footer(app) -> list[str]:
    from review.core.selection_controller import set_selected_wells
    from review.persistence import view_state

    ctx = app._ctx
    view_state._activate_tab(ctx, "Bar Plots")
    set_selected_wells(ctx, {"A01", "B01"})
    ctx.features.redraw(ctx, "Bar Plots")
    fig = ctx.runtime.barplot.fig
    return [t.get_text() for t in fig.texts if t.get_gid() == "observations_footer"]


@pytest.mark.slow
def test_without_the_feature_a_nanowell_plate_loads_says_so_and_offers_no_nanowell_views(
        qtbot, monkeypatch, tmp_path):
    from review.core import observation_band as ob
    from review.features.image_browser.review_image import renderer as rr
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    for m in [n for n in list(sys.modules) if n.startswith("review.features.nanowell")]:
        monkeypatch.delitem(sys.modules, m, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_ImportBlocker("nanowell"), *sys.meta_path])
    rt = _prune_registry(monkeypatch, "Nanowells")
    app = _build(qtbot, rt, root)
    _exercise(app)
    ctx = app._ctx
    assert app._features.get("Nanowells") is None
    assert ctx.state.dataset.in_dir == root / "in"
    assert ob.band_mode(ctx, "Scatter Plot") == "hidden"
    assert ctx.widgets.get("_plotting_observations_grp").isHidden()
    assert _bar_footer(app) == [
        "Observations: cells · pooled from 3–4 occupied nanowells per well · nanowell views are not in this build"]
    ctx.state.channel.active_image_channel = "phase"
    rr.update_preview_ctx(ctx, "A01")
    rr.refresh_review_image_ctx(ctx)
    assert ctx.widgets.get("_review_image_status").text() == rr.NO_LABEL_MAP_NANOWELL.format(
        overlays=rr.OVERLAYS_NOT_IN_BUILD)
    from review.data.dataset_dims import channel_options
    assert channel_options(ctx)[0] == "MONTAGE"


@pytest.mark.slow
def test_with_the_feature_out_opened_directly_finds_in_and_offers_nanowell_observations(qtbot, tmp_path):
    import review.runtime_app as rt
    from review.core import observation_band as ob
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    app = _build(qtbot, rt, root / "out")
    ctx = app._ctx
    assert ctx.state.dataset.in_dir == root / "in"
    assert ob.band_mode(ctx, "Scatter Plot") == "toggle"
    assert _bar_footer(app) == ["Observations: cells · pooled from 3–4 occupied nanowells per well"]
