"""Audit X16 — full tracebacks go to the dev-gated logger, not the UI panel.

``ScatterCellViewer._load_cell_data`` used to dump the entire
``traceback.format_exc()`` into the user-visible diagnostics panel. The panel
now shows a concise one-liner; the full stack is routed to the module logger at
DEBUG (only visible when ALLWELL logging is turned up).
"""
from __future__ import annotations

import logging
import types

from review.features.scatter.cell_viewer_view import ScatterCellViewer


def _make_viewer():
    # Skip the QWidget __init__ (needs a QApplication) — we only exercise the
    # error-routing branch of _load_cell_data.
    v = ScatterCellViewer.__new__(ScatterCellViewer)
    v._debug_lines = []
    v._debug_text = None
    v._img_label = types.SimpleNamespace(setText=lambda _t: None)
    return v


def test_traceback_not_in_panel_concise_message_is(caplog):
    v = _make_viewer()

    def _boom():
        raise RuntimeError("kaboom-unique-marker")

    v._load_cell_data_impl = _boom

    with caplog.at_level(logging.DEBUG,
                         logger="review.features.scatter.cell_viewer_view"):
        v._load_cell_data()

    panel = "\n".join(v._debug_lines)
    # Concise, user-facing line is present…
    assert "kaboom-unique-marker" in panel
    # …but the raw multi-line traceback is NOT dumped into the panel.
    assert "Traceback (most recent call last)" not in panel
    # The full stack went to the dev-gated DEBUG log instead.
    assert any("Traceback (most recent call last)" in r.getMessage()
               for r in caplog.records)
