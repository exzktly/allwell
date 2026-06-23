"""Saved Plots PDF report builder + image-grid downsample dpi."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import numpy as np  # noqa: E402

from well_viewer.features.saved_plots._report import build_saved_plots_report  # noqa: E402


def _rec(rid, title, tab, summary):
    return SimpleNamespace(id=rid, title=title, tab=tab,
                           created_at="2026-01-01T10:00:00", summary=summary)


def test_report_writes_valid_multipage_pdf(tmp_path):
    img = tmp_path / "a.png"
    mpimg.imsave(str(img), (np.random.rand(30, 50, 3) * 255).astype("uint8"))
    recs = [
        _rec("1", "Bar A", "Bar Plots", "Tab: Bar Plots\nWells (2): A01, B01"),
        _rec("2", "Missing", "Line Graphs", "Tab: Line Graphs"),  # no image
    ]
    paths = {"1": img, "2": tmp_path / "nope.png"}
    out = tmp_path / "report.pdf"

    n = build_saved_plots_report(
        recs, lambda r: paths[r.id], out,
        dataset_name="demo", summary_for=lambda r: r.summary)

    assert n == 2
    assert out.exists()
    assert out.read_bytes()[:5] == b"%PDF-"
    assert out.stat().st_size > 1000


def test_report_empty_records_still_writes_cover(tmp_path):
    out = tmp_path / "empty.pdf"
    n = build_saved_plots_report([], lambda r: None, out, dataset_name="d")
    assert n == 0
    assert out.read_bytes()[:5] == b"%PDF-"


def test_downsample_dpi_caps_longest_side():
    from well_viewer.features.image_browser.image_table.export import _downsample_dpi

    # 10in wide at 300dpi would be 3000px → capped to 1600/10 = 160 dpi.
    assert _downsample_dpi(300, 10.0, 4.0, max_px=1600) == 160
    # Never exceeds the original dpi (small figure).
    assert _downsample_dpi(120, 2.0, 2.0, max_px=1600) == 120
    # Never below the floor.
    assert _downsample_dpi(300, 100.0, 100.0, floor=60) == 60
