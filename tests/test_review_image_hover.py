"""Segmentation-tab hover pixel-value readout (`_show_image_pixel_tooltip`).

Guards the hover regression: the tooltip must report the value of the
*displayed* array at the hovered pixel, survive a missing ``_raw_arr`` handle
(falling back to ``last_fluor_arr``), and tolerate an unset active channel.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QToolTip  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402

from well_viewer.features.review_image.controller import _show_image_pixel_tooltip  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _Evt:
    def __init__(self, x, y):
        self._p = QPointF(x, y)

    def position(self):
        return self._p


def _stub_app(last_fluor_arr=None):
    return SimpleNamespace(
        _np=np,
        _NP_AVAILABLE=True,
        _app_state=SimpleNamespace(
            review_image=SimpleNamespace(last_fluor_arr=last_fluor_arr),
        ),
    )


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(QToolTip, "showText", staticmethod(lambda *a, **k: calls.append(a)))
    monkeypatch.setattr(QToolTip, "hideText", staticmethod(lambda *a, **k: None))
    return calls


def test_hover_reports_displayed_pixel_value(monkeypatch):
    calls = _capture(monkeypatch)
    lbl = QLabel()
    lbl.resize(40, 40)
    arr = np.arange(100, dtype=np.float32).reshape(10, 10)  # value at (5,5) == 55
    lbl._raw_arr = arr
    lbl._sz_w = 40
    lbl._sz_h = 40  # 4x scale, image fills the label
    _show_image_pixel_tooltip(_stub_app(), _Evt(20, 20), "GFP", lbl)
    assert calls, "hover should show a tooltip over the image"
    text = calls[-1][1]
    assert "x=5" in text and "y=5" in text and "GFP:55" in text


def test_hover_falls_back_to_last_fluor_arr(monkeypatch):
    calls = _capture(monkeypatch)
    lbl = QLabel()
    lbl.resize(40, 40)
    lbl._sz_w = 40
    lbl._sz_h = 40
    # No _raw_arr on the label → must use state.last_fluor_arr.
    arr = np.full((10, 10), 7.0, dtype=np.float32)
    _show_image_pixel_tooltip(_stub_app(last_fluor_arr=arr), _Evt(20, 20), "RFP", lbl)
    assert calls and "RFP:7" in calls[-1][1]


def test_hover_no_array_hides(monkeypatch):
    calls = _capture(monkeypatch)
    lbl = QLabel()
    lbl.resize(40, 40)
    # Neither handle nor fallback → no value tooltip (hideText, no crash).
    _show_image_pixel_tooltip(_stub_app(last_fluor_arr=None), _Evt(20, 20), "GFP", lbl)
    assert not calls
