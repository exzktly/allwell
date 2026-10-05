"""Regression test for the figure-export format support.

Bug (found in testing): the Export… window only allowed saving as PNG. The
dialog must offer every supported format — PNG, TIFF, SVG, EPS and PDF — and
each must actually write a file.

Also covers Copy SVG: ``copy_figure_to_clipboard`` must produce true vector
output. On macOS it writes ``NSPasteboard`` natively (so Illustrator pastes
editable vector); off macOS — including in CI — it falls back to a Qt
clipboard payload carrying PDF + SVG + a raster image.
"""

from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from widgets.fig_clipboard import (
    _macos_pasteboard_write,
    _render_figure,
    copy_figure_to_clipboard,
)
from review.features.export.sidebar_view import (
    _EXPORT_FILTER_ORDER,
    _EXPORT_FILTERS,
    ExportStyleSidebar,
)

_EXPECTED = {"png", "tiff", "svg", "eps", "pdf"}


def test_export_dialog_offers_every_format():
    """The Export… file dialog enumerates all five formats."""
    assert set(_EXPORT_FILTERS) == _EXPECTED
    assert set(_EXPORT_FILTER_ORDER) == _EXPECTED
    # Every format has a non-empty Qt filter string with its extension.
    for fmt, flt in _EXPORT_FILTERS.items():
        assert f"*.{fmt[:3]}" in flt or f"*.{fmt}" in flt


@pytest.mark.parametrize("fmt", sorted(_EXPECTED))
def test_write_figure_saves_each_format(fmt, tmp_path):
    """``_write_figure`` produces a non-empty file for every format."""
    fig = plt.figure()
    fig.add_subplot(1, 1, 1).plot([0, 1, 2], [0, 1, 4])
    try:
        class _Stub:
            _fig = fig

        out = tmp_path / f"figure.{fmt}"
        ExportStyleSidebar._write_figure(_Stub(), out, fmt)
        assert out.exists() and out.stat().st_size > 0
    finally:
        plt.close(fig)


@pytest.mark.parametrize("fmt", ["svg", "pdf", "png"])
def test_render_figure_produces_bytes(fmt):
    """``_render_figure`` encodes the figure to each clipboard format."""
    fig = plt.figure()
    fig.add_subplot(1, 1, 1).plot([0, 1, 2], [0, 1, 4])
    try:
        data = _render_figure(fig, fmt, {"bbox_inches": "tight"}, {})
        assert data and isinstance(data, bytes)
    finally:
        plt.close(fig)


def test_copy_figure_to_clipboard_carries_vector(qapp):
    """The Qt fallback clipboard carries editable PDF + SVG plus a raster.

    On macOS the real path is the native ``NSPasteboard`` write; in CI this
    exercises the Qt fallback, which must still carry vector flavours so
    Copy never degrades to raster-only.
    """
    fig = plt.figure()
    fig.add_subplot(1, 1, 1).plot([0, 1, 2], [0, 1, 4])
    try:
        status = copy_figure_to_clipboard(fig)
        assert isinstance(status, str) and status
        assert not status.startswith("Copy failed")
        from PySide6.QtWidgets import QApplication
        mime = QApplication.clipboard().mimeData()
        assert mime.hasFormat("application/pdf")
        assert mime.hasFormat("image/svg+xml")
        svg = bytes(mime.data("image/svg+xml")).decode("utf-8", "replace")
        assert "<svg" in svg                   # real SVG markup, not a blob
        assert mime.hasImage()                 # raster fallback present
    finally:
        plt.close(fig)


def test_macos_pasteboard_write_declines_off_darwin():
    """Off macOS the native pasteboard write declines so the Qt fallback runs."""
    if sys.platform == "darwin":
        pytest.skip("native pasteboard path is exercised only on macOS")
    assert _macos_pasteboard_write(b"%PDF-1.4", b"<svg/>", b"\x89PNG") is False


