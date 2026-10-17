"""The Playground's Nanowell array buttons, pressed for real, fill the window with the Analyze settings.

The tab is built by its real constructor against the minimal shell, the frame is a synthetic nanowell array
opened through Open image…'s own path, the buttons are clicked, and the search runs on its real worker thread.
The assertions are at the boundary (CLAUDE.md §7.4 #1, §7.6): the rows of the window's table, the words on its
status line, the read-out in the dock. A button wired to nothing, a search that never comes back, or a window
that shows a different answer than the compute produced all fail here.
"""

from __future__ import annotations

import io
import json
import os
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

pytest.importorskip("PySide6")
tifffile = pytest.importorskip("tifffile")

import review.features.image_browser.playground.controller as pc  # noqa: E402
from tests._nanowell_synth import TilePlan, render_tiles  # noqa: E402

PX = 4.3


@pytest.fixture
def playground(shell_ctx):
    from PySide6.QtWidgets import QHBoxLayout, QWidget
    from review.features.image_browser.playground.tab_view import build_playground_tab

    ctx, host = shell_ctx
    frame = QWidget()
    QHBoxLayout(frame)
    build_playground_tab(ctx, frame)
    host._test_playground_frame = frame
    yield ctx
    popup = getattr(ctx.runtime.playground, "nanowell_popup", None)
    if popup is not None:
        popup.close()


def _array_image(**render) -> np.ndarray:
    plan = TilePlan(1, 1, (900, 900), 0.0, PX)
    return render_tiles(plan, pitch_um=172.0, diameter_um=100.0, radius_um=1300.0, noise_sigma=0.05,
                        blur_px=1.0, **render).images["F0"]


def _table(popup) -> dict[str, str]:
    table = popup._table
    return {table.item(r, 0).text(): table.item(r, 1).text() for r in range(table.rowCount())}


def _wait_for_window(ctx, qtbot):
    qtbot.waitUntil(lambda: getattr(ctx.runtime.playground, "nanowell_popup", None) is not None
                    and not getattr(ctx.runtime.playground, "nanowell_running", True), timeout=60000)
    return ctx.runtime.playground.nanowell_popup


def test_from_this_frame_shows_the_square_lattice_s_settings(playground, tmp_path, qtbot):
    ctx = playground
    path = tmp_path / "array.tif"
    # A square lattice, so the right answer (Infer) differs from the form's default geometry.
    tifffile.imwrite(path, _array_image(lattice="square", array_shape="square", rotation_deg=-15.0,
                                        square_centre=False))
    pc.playground_open_loose_image(ctx, str(path))
    ctx.widgets.get("_playground_nw_px_edit").setText(f"{PX}")
    ctx.widgets.get("_playground_nw_frame_btn").click()
    popup = _wait_for_window(ctx, qtbot)
    rows = _table(popup)
    assert rows["Plate format"] == "100 µm nanowells"
    assert rows["Array geometry"] == "Infer from the image"
    assert rows["Lattice pitch (µm)"] == "(blank)"
    assert "array.tif" in popup._status.text()
    assert "square, pitch 172.0" in popup._facts.text()
    assert popup._copy.isEnabled() and "Array geometry: Infer from the image" in popup._text
    assert ctx.widgets.get("_playground_nw_frame_btn").isEnabled(), "the button stayed disabled after the search"


def test_from_this_frame_without_a_pixel_size_asks_for_one_and_searches_nothing(playground, tmp_path):
    ctx = playground
    path = tmp_path / "array.tif"
    tifffile.imwrite(path, _array_image())
    pc.playground_open_loose_image(ctx, str(path))
    ctx.widgets.get("_playground_nw_frame_btn").click()
    assert "pixel size" in ctx.window._playground_status.text()
    assert getattr(ctx.runtime.playground, "nanowell_popup", None) is None
    assert not getattr(ctx.runtime.playground, "nanowell_running", False)


def test_the_well_archive_button_stitches_the_chosen_archive(playground, tmp_path, qtbot, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from tests._nanowell_run_fixture import build_well

    ctx = playground
    rendered, record = build_well(rows=2, cols=2, rotation=9.0, radius_fraction=0.3)
    archive = tmp_path / "A01.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, image in rendered.images.items():
            buffer = io.BytesIO()
            tifffile.imwrite(buffer, image)
            zf.writestr(f"exp_Phase_A01_{name}_T0.tif", buffer.getvalue())
    (tmp_path / "acquisition.json").write_text(json.dumps(record))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(archive), "")))
    ctx.widgets.get("_playground_nw_archive_btn").click()
    popup = _wait_for_window(ctx, qtbot)
    rows = _table(popup)
    assert rows["Plate format"] == "100 µm nanowells"
    assert rows["Array geometry"] == "Hexagonal lattice in a circle"
    assert "well A01" in popup._status.text() and "stitched" in popup._status.text()
    assert "4 (2 × 2 grid)" in popup._facts.text()


def test_a_refusal_is_shown_as_its_reason_with_nothing_to_copy(playground, tmp_path, qtbot):
    ctx = playground
    path = tmp_path / "flat.tif"
    tifffile.imwrite(path, np.random.default_rng(0).normal(0.3, 0.02, (300, 300)).astype(np.float32))
    pc.playground_open_loose_image(ctx, str(path))
    ctx.widgets.get("_playground_nw_px_edit").setText("1")
    ctx.widgets.get("_playground_nw_frame_btn").click()
    popup = _wait_for_window(ctx, qtbot)
    assert "No nanowells were found" in popup._status.text()
    assert popup._table.rowCount() == 0 and not popup._copy.isEnabled()
    assert "No nanowell array" in ctx.window._playground_status.text()


def test_the_overlay_rings_every_site_and_marks_the_origin():
    from review.features.image_browser.playground import nanowell_compute as nc
    from review.features.image_browser.playground.nanowell_view import overlay_marks

    s = nc.suggest_from_image(_array_image(rotation_deg=12.0), PX)
    marks = overlay_marks(s)
    kinds = [m.kind for m in marks]
    assert kinds.count("found") == len(s.roster_px) and kinds.count("dark") == 0
    assert kinds.count("origin") == kinds.count("square") == kinds.count("outline") == 1
    origin = next(m for m in marks if m.kind == "origin")
    assert (origin.x, origin.y) == pytest.approx((450.0, 450.0), abs=2.0)
