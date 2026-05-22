"""Regression test for the figure-export format support.

Bug (found in testing): the Export… window only allowed saving as PNG. The
dialog must offer every supported format — PNG, TIFF, SVG, EPS and PDF — and
each must actually write a file.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from well_viewer.figure_export_editor import figure_to_vector_mimedata  # noqa: E402
from well_viewer.views.export_style_sidebar_view import (  # noqa: E402
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


def test_copy_svg_clipboard_is_vector_only():
    """Copy SVG must put vector data (SVG + PDF) on the clipboard and *no*
    raster flavour — a clipboard carrying both makes Illustrator paste the
    raster, which is the bug this guards against.
    """
    fig = plt.figure()
    fig.add_subplot(1, 1, 1).plot([0, 1, 2], [0, 1, 4])
    try:
        mime = figure_to_vector_mimedata(fig)
        assert mime.hasFormat("image/svg+xml")
        assert mime.hasFormat("application/pdf")
        assert not mime.hasImage()             # no raster QImage flavour
        assert not mime.hasFormat("image/png")
        svg = bytes(mime.data("image/svg+xml")).decode("utf-8", "replace")
        assert "<svg" in svg                   # real SVG markup, not a blob
    finally:
        plt.close(fig)
