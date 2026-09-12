"""S4 — the Image Table inherits the shared per-channel brightness default.

A brightness tuned for a channel (e.g. in Review Image, stored on the shared
``AppState.image_view``) becomes the default for an Image Table cell showing that
channel when the channel has no explicit LUT window. A per-channel ``channel_opts``
min/max (set on the builder shelf's ⚙) overrides it.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app(qtbot, *, channel_opts=None, channel="gfp"):
    from review.core.image_view_state import ImageViewState
    from review.features.image_browser.image_table.state import ImageTableState

    it_state = ImageTableState(rows=1, cols=1)
    it_state.channel_opts = channel_opts or {}
    app = SimpleNamespace(
        _image_table_cells=[[{"well": "A01", "chan": channel, "tp": "0", "fov": "1"}]],
        _app_state=SimpleNamespace(image_view=ImageViewState(), image_table=it_state),
    )
    from review.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state,
                               runtime=AppRuntime(app))
    return app


def test_auto_cell_inherits_shared_channel_lut(qtbot):
    from review.features.image_browser.image_table.image_compose import _parse_lut

    app = _app(qtbot)  # no channel_opts window → inherit the shared default
    app._app_state.image_view.set_lut("gfp", 5.0, 200.0)
    assert _parse_lut(app._ctx, 0) == (5.0, 200.0)


def test_channel_opts_window_overrides_shared(qtbot):
    from review.features.image_browser.image_table.image_compose import _parse_lut

    app = _app(qtbot, channel_opts={"gfp": {"lut_min": 10.0, "lut_max": 90.0}})
    app._app_state.image_view.set_lut("gfp", 5.0, 200.0)
    # The channel's own window wins over the shared per-channel default.
    assert _parse_lut(app._ctx, 0, "gfp") == (10.0, 90.0)


def test_auto_cell_without_shared_falls_back_to_autofit(qtbot):
    from review.features.image_browser.image_table.image_compose import _parse_lut

    app = _app(qtbot)  # no shared LUT set, no channel_opts window
    assert _parse_lut(app._ctx, 0) == (None, None)
