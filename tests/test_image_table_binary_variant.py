"""Image Table binary channel variants — "<CHAN> (binary)" axis values.

A binary variant is a virtual channel offered alongside the real channels so a
nested row axis can mix e.g. NIR, GFP and GFP (binary). It flows through the axis
picker / product like any channel token; the leaf render/load helpers decode it
(load the base channel, force threshold-binary). These checks cover the option
list + product plumbing and the token decode.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import controller as C  # noqa: E402
from well_viewer.features.image_browser.image_table.image_compose import (  # noqa: E402
    _binary_base, _binary_variant, _is_binary_variant,
)
from well_viewer.features.image_browser.image_table.state import ImageTableState  # noqa: E402


def _fake_app(*, channels, active_channels=None):
    info = {"available_timepoints": [], "available_fovs": [], "nuclear_token": ""}
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={"A01": "A01"}, pipeline_info=info, cache={}),
        channel=SimpleNamespace(fluor_channels=list(channels), smfish_channels=set(),
                                ratio_metrics=[], seg_channel_token=""),
        image_table=ImageTableState(active_channels=set(active_channels or [])),
    )
    app._parse_rc = lambda w: (w[0], int(w[1:]))
    from well_viewer.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state, runtime=AppRuntime(app))
    return app


def test_channel_options_include_binary_variants():
    app = _fake_app(channels=["gfp", "nir"])
    opts = C._channel_options(app._ctx)
    assert "GFP" in opts and "NIR" in opts
    assert "GFP (binary)" in opts and "NIR (binary)" in opts
    # variants come after the real channels
    assert opts.index("GFP (binary)") > opts.index("NIR")


def test_binary_variant_selectable_on_channel_axis():
    app = _fake_app(channels=["gfp", "nir"],
                    active_channels={"NIR", "GFP", "GFP (binary)"})
    vals = C._axis_values(app._ctx, "channel")
    # ticked channels in option order — the binary variant included
    assert vals == ["GFP", "NIR", "GFP (binary)"]
    prod = C._product_values(app._ctx, ["well", "channel"])
    assert ("A01", "GFP (binary)") in prod


def test_binary_variants_are_opt_in_not_default():
    # No channel selection → the axis is the real channels only (variants excluded),
    # so a plain "Channels × Time" build isn't silently doubled.
    app = _fake_app(channels=["gfp", "nir"])
    vals = C._axis_values(app._ctx, "channel")
    assert vals == ["GFP", "NIR"]
    assert not any(_is_binary_variant(v) for v in vals)


def test_token_decode_roundtrip():
    assert _binary_variant("GFP") == "GFP (binary)"
    assert _is_binary_variant("GFP (binary)") and not _is_binary_variant("GFP")
    assert _binary_base("GFP (binary)") == "GFP"
    assert _binary_base("GFP") == "GFP"


def test_build_and_render_with_binary_variant_row(image_table_app):
    """End-to-end on the real tab: a Wells › [chan, chan (binary)] × Time layout
    puts the variant token in the selector grid and renders without raising."""
    app = image_table_app
    C.image_table_repopulate_dropdowns(app._ctx)
    opts = C._channel_options(app._ctx)
    real = [o for o in opts if not _is_binary_variant(o) and o != "NUC+SEG"]
    assert real, "need at least one real channel"
    base = real[0]
    variant = _binary_variant(base)

    st = app._app_state.image_table
    st.active_channels = {base, variant}
    C.image_table_apply_dims_preset(app._ctx, ["well", "channel"], ["timepoint"])

    cells = app._ctx.runtime.image_table.cells
    grid_chans = {cells[r][0]["chan"] for r in range(len(cells))}
    assert variant in grid_chans, f"binary variant missing from grid: {grid_chans}"

    # Render must not raise; the binary decode loads the base channel + forces
    # threshold mode downstream.
    C.image_table_generate(app._ctx)
    assert app._app_state.image_table.last_render
