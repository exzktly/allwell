"""Test detection's nanowell outlines can be turned off, to see the montage they cover.

Asked for: "Be able to toggle show/hide colored well overlays on test detection." The window's **Nanowell
outlines** box, ticked by default, redraws the result already computed with or without the outlines — no re-run.
Driven through the real window opened by the form's Test detection button.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_form import _plate, _site, _use_nanowell_schema, tab  # noqa: E402,F401


def test_unticking_the_box_redraws_the_montage_alone_and_ticking_it_brings_the_outlines_back(tab, qtbot, tmp_path):  # noqa: F811
    from PySide6.QtGui import QColor

    from pipeline.nanowell_preview import PreviewResult

    root, _ = _plate(tmp_path)
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_preview_btn.click()
    window = tab._nanowell_preview_window
    result = PreviewResult("ok", montage=np.zeros((100, 200), np.uint8), um_per_px=4.0, origin_um=(0.0, 0.0))
    result.sites = [_site(3, 4, "empty", x=400.0, y=200.0)]          # centre (100, 50) px, radius 12.5 px
    window._zoom_cb.setCurrentIndex(window._zoom_cb.findData(1.0))
    window._result = result

    from analyze.nanowell_preview import render_montage

    outlined = render_montage(result, 1.0).toImage()
    plain = render_montage(result, 1.0, show_sites=False).toImage()
    assert plain.pixelColor(112, 50) == QColor(0, 0, 0) and outlined.pixelColor(112, 50) != QColor(0, 0, 0)

    def shown():
        return window._montage_label.pixmap().toImage()

    assert window._sites_cb.isChecked()
    window._draw()
    assert shown() == outlined
    window._sites_cb.setChecked(False)                    # redrawn from the result held: no re-run
    assert shown() == plain
    assert {plain.pixelColor(x, y).rgb() for x in range(200) for y in range(100)} == {QColor(0, 0, 0).rgb()}
    window._sites_cb.setChecked(True)
    assert shown() == outlined
    window.close()
