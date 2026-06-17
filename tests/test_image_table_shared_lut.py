"""S4 — the Image Table inherits the shared per-channel brightness default.

A brightness tuned for a channel (e.g. in Review Image, stored on the shared
``AppState.image_view``) becomes the default for an *auto* Image Table row
showing that channel. A manual per-row min/max still overrides it.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app(qtbot, *, min_text="auto", max_text="auto", channel="gfp"):
    from PySide6.QtWidgets import QComboBox, QLineEdit
    from well_viewer.core.image_view_state import ImageViewState

    cb = QComboBox(); cb.addItem(channel); cb.setCurrentText(channel)
    return SimpleNamespace(
        _image_table_lut={0: {"min": QLineEdit(min_text), "max": QLineEdit(max_text)}},
        _image_table_cells=[[{"chan_cb": cb}]],
        _app_state=SimpleNamespace(image_view=ImageViewState()),
    )


def test_auto_row_inherits_shared_channel_lut(qtbot):
    from well_viewer.features.image_table.image_compose import _parse_lut

    app = _app(qtbot)
    app._app_state.image_view.set_lut("gfp", 5.0, 200.0)
    assert _parse_lut(app, 0) == (5.0, 200.0)


def test_manual_row_value_overrides_shared(qtbot):
    from well_viewer.features.image_table.image_compose import _parse_lut

    app = _app(qtbot, min_text="10", max_text="90")
    app._app_state.image_view.set_lut("gfp", 5.0, 200.0)
    assert _parse_lut(app, 0) == (10.0, 90.0)


def test_auto_row_without_shared_falls_back_to_autofit(qtbot):
    from well_viewer.features.image_table.image_compose import _parse_lut

    app = _app(qtbot)  # no shared LUT set
    assert _parse_lut(app, 0) == (None, None)
