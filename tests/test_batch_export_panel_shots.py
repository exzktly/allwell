"""Every mode's OUTPUT SETTINGS builds and carries the shared controls — and a
picture of each, for showing someone the interface.

Two things at once, and both are worth having. The **assertions** are the half
the fast lane cannot make: it builds no application, so a ``_build_output_panel``
that raises on one mode would render as an empty page with every fast test
green (§7.8 rule 3). Driving each of the five modes through the real
constructor and then reading the controls off the built panel is what notices.

The **images** are the other half. They go to ``SHOT_DIR`` (outside the
repository — nothing here writes into the working tree), and they are the
widgets as the application really lays them out rather than a mock-up of them,
so a screenshot shown to a reader cannot drift from what ships.

Auto-marked ``slow`` — it builds a full ``WellViewerApp`` per mode — so run it
deliberately rather than as part of the lane:

    ALLWELL_SHOT_DIR=/tmp/shots python -m pytest \\
        tests/test_batch_export_panel_shots.py -m "" -q
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

SHOT_DIR = Path(os.environ.get("ALLWELL_SHOT_DIR", "/tmp/allwell-shots"))

#: ``(mode key, file stem)`` — every mode the bar offers.
_MODES = (
    ("line", "01-line"),
    ("bar", "02-bar"),
    ("scatter_cells", "03-scatter-cells"),
    ("scatter_agg", "04-scatter-aggregate"),
    ("response", "05-response"),
)


def _panel_for(app, mode):
    app._notebook.setCurrentByName("Batch Export")
    app._ctx.runtime.batch_export.set_mode(mode)
    state = app._ctx.runtime.batch_export.inline_state
    return state["panels"][mode]


def _fill_axes_demo(panel):
    """Type something into the AXES block so the shot shows it in use.

    An empty form shows the controls; a filled one shows what they mean. The
    values are the ones the README uses as its example, so the picture and the
    prose agree.
    """
    # The X row is absent where the mode's x is categorical (the bar stack),
    # and the scale combos are absent where the mode owns a lossless log of
    # its own (the scatter modes). Both are deliberate, so the shot fills what
    # the mode offers rather than asserting every mode offers everything.
    if panel._x_min_edit is not None:
        panel._x_min_edit.setText("0")
        panel._x_max_edit.setText("48")
    rows = panel._panel_rows
    first = next(iter(rows))
    box, lo, hi, scale = rows[first]
    lo.setText("0")
    hi.setText("2500")
    if scale is not None:
        scale.setCurrentIndex(2)      # Log
    if len(rows) > 1:
        keys = list(rows)
        # Untick the last panel, so the shot shows a mode with one dropped.
        last_box = rows[keys[-1]][0]
        if last_box is not None:
            last_box.setChecked(False)
        mid_lo, mid_hi = rows[keys[1]][1], rows[keys[1]][2]
        mid_lo.setText("0")
        mid_hi.setText("1")
    if getattr(panel, "_cell_rows_cb", None) is not None:
        panel._cell_rows_cb.setChecked(True)
    if getattr(panel, "_to_files_cb", None) is not None:
        panel._to_files_cb.setChecked(True)
    if getattr(panel, "_to_gallery_cb", None) is not None:
        panel._to_gallery_cb.setChecked(True)
    if getattr(panel, "_to_deck_cb", None) is not None:
        panel._to_deck_cb.setChecked(True)


@pytest.mark.parametrize("mode,stem", _MODES)
def test_render_batch_export_panel(loaded_app, mode, stem):
    from PySide6.QtWidgets import QApplication

    panel = _panel_for(loaded_app, mode)
    _fill_axes_demo(panel)

    # The right-hand OUTPUT SETTINGS column is what the controls live in; the
    # left is the plate-map group editor, unchanged by this work.
    target = getattr(panel, "_split_right", panel)
    # Wide and tall enough that no row has to overlap its neighbour: the
    # stacked modes carry four AXES rows more than the single-panel ones, and
    # a target left at its own size squeezes them into each other.
    target.setMinimumWidth(820)
    target.setMinimumHeight(max(560, target.sizeHint().height() + 40))
    target.adjustSize()
    target.layout().activate()
    QApplication.processEvents()

    # The one assertion this slow-lane file makes about behaviour rather than
    # about a file existing: the run summary is derived, and the fast lane
    # drives it through a stub. Here it is the real panel, built by the real
    # constructor, with the real controls ticked by ``_fill_axes_demo`` — so a
    # summary that never got wired, or one wired to a control this mode does
    # not build, shows up as an empty or stale block.
    summary = panel._summary_lbl.text()
    assert summary.startswith("Each export group produces:"), summary
    # And the destination row the summary is derived from really is on the
    # panel: three boxes, none of them privileged.
    assert panel._to_files_cb.isChecked()
    assert panel._to_files_cb.text() == "Image files"
    assert "One raw per-cell CSV" in summary, summary
    assert ("The run also writes one PowerPoint deck into this folder, a "
            "slide per group, and files a Saved Plots record per figure."
            in summary), summary

    # Untick the folder and the same real panel says what it will not save,
    # and greys the controls that then decide nothing. Driven through the
    # widget rather than the predicate, because the wiring between them is
    # what the fast lane cannot see.
    panel._to_files_cb.setChecked(False)
    QApplication.processEvents()
    fileless = panel._summary_lbl.text()
    assert ("No image files, and no CSVs: the numbers behind each figure are "
            "not saved anywhere." in fileless), fileless
    assert not panel._fmt_cb.isEnabled()
    assert not panel._cell_rows_cb.isEnabled()
    if panel._to_deck_cb is not None and panel._to_deck_cb.isChecked():
        assert panel._out_dir_edit.isEnabled(), "the deck still needs a folder"
    if panel._to_deck_cb is not None:
        panel._to_deck_cb.setChecked(False)
        QApplication.processEvents()
        assert not panel._out_dir_edit.isEnabled(), \
            "a gallery-only run needs no output folder"

    # Re-tick, and check it all comes back. Without these the toggling above
    # proved only that the controls can be turned off: a `_wants_image_files`
    # that latched — the "works once" bug — passed every assertion here, on
    # all five panels, and the fast lane cannot see it because each of its
    # stubs is fresh.
    panel._to_files_cb.setChecked(True)
    if panel._to_deck_cb is not None:
        panel._to_deck_cb.setChecked(True)
    QApplication.processEvents()
    restored = panel._summary_lbl.text()
    assert restored.startswith("Each export group produces:"), restored
    assert "not saved anywhere" not in restored, restored
    assert panel._fmt_cb.isEnabled()
    assert panel._cell_rows_cb.isEnabled()
    assert panel._out_dir_edit.isEnabled()
    if len(panel._PANELS) > 1:
        # ``_fill_axes_demo`` unticks the last panel on every stacked mode.
        kept = len(panel._PANELS) - 1
        assert f"{kept} of {len(panel._PANELS)} panels" in summary, summary
        assert panel._PANELS[-1].label not in summary, summary

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = SHOT_DIR / f"{stem}.png"
    assert target.grab().save(str(out)), out
    assert out.stat().st_size > 0

    # The controls this work added really are on every mode's panel — which is
    # the half of this file that is a test rather than a picture.
    assert panel._panel_rows, mode
    assert hasattr(panel, "_x_min_edit") and hasattr(panel, "_x_scale_cb")
    assert hasattr(panel, "_cell_rows_cb")
