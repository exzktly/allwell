"""An observations footer wider than its figure wraps inside it, so a capture keeps the plot's shape.

The footer was one unwrapped ``fig.text`` per line. A line wider than the figure ran off the canvas's right edge,
where the screen merely clipped it; a capture crops ``bbox_inches="tight"``, and the tight box grew to take the
whole line in. Saving a Nanowells distribution to Saved Plots then handed back an image as wide as its longest
footer note, with the plot squeezed against the left edge of it. Every footer now goes through
``observation_band.write_footer``, which wraps to the figure's width.
"""

from __future__ import annotations

import ast
import io

import pytest

pytest.importorskip("PySide6")

from tests import _repo_scan  # noqa: E402
from tests.test_plots_draw_nanowell_well_medians import _footer, _shell  # noqa: E402

#: Long enough to be several figure widths at the footer's 7 pt.
LONG_NOTE = "Registration was checked against the reference well and found to drift at the rim; " * 4


def test_saving_a_nanowell_distribution_keeps_the_figure_s_shape_and_the_whole_footer(qtbot, tmp_path, monkeypatch):
    from PIL import Image

    from review.domain import observation
    from review.features.distribution.controller import redraw_distribution

    real = observation.footer_line
    monkeypatch.setattr(observation, "footer_line", lambda *a, **k: real(*a, **k) + " · " + LONG_NOTE)
    ctx = _shell(qtbot, tmp_path, "review.features.distribution.tab_view.build_distribution_tab", "dist_save")
    redraw_distribution(ctx)
    fig = ctx.widgets.get("_distribution_fig")
    assert LONG_NOTE.strip() in _footer(fig), "the probe note did not reach the footer, so nothing is measured"

    card = ctx.widgets.get("_distribution_card")
    saved = []
    card.set_save_handler(lambda png, label, svg=b"", panel=False: saved.append(png) or "")
    card.saveFigureToGallery()
    assert saved and saved[0], "the card's save produced no image"

    width, height = Image.open(io.BytesIO(saved[0])).size
    fig_w, fig_h = fig.get_size_inches()
    # A tight crop is the ink plus matplotlib's 0.1 in pad on each side, and the wrapped footer's ink reaches the
    # figure's right edge, so the image may be up to that pad wider than the figure — no more. Unwrapped, this
    # footer made it 5,516 px for a 2,160 px figure.
    pad_px = 2 * 0.1 * 300
    assert width <= fig_w * 300 + pad_px, f"saved {width} px wide for a {fig_w * 300:.0f} px figure"
    assert width / height == pytest.approx(fig_w / fig_h, rel=0.15)
    # Wrapped, not cut: the footer still holds every word.
    assert LONG_NOTE.strip() in _footer(fig)


def test_every_observations_footer_is_written_by_the_one_helper():
    """A footer written with its own ``fig.text(..., gid=FOOTER_GID)`` would not wrap; the helper is the only site."""
    sites = []
    for path, tree in _repo_scan.parsed_sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "gid" and isinstance(kw.value, ast.Name) and kw.value.id == "FOOTER_GID":
                    sites.append(f"{_repo_scan.rel(path)}:{node.lineno}")
    assert len(sites) == 1 and sites[0].startswith("review/core/observation_band.py:"), (
        "observations footers written outside observation_band.write_footer: " + ", ".join(sites))
