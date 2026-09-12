"""Well-label text fields persist on commit (Enter / focus-out), not per
keystroke.

main's v6.1.2 autosave wired the label grid's per-keystroke ``textChanged`` to
``schedule_save("sample_definitions")`` — so every letter rewrote
pipeline_info.json. The save now fires on ``editingFinished`` instead, gated by a
dirty flag so tabbing through cells doesn't churn the file.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.qt

pytest.importorskip("PySide6")

from review.features.home.labels.tab_view import LabelGrid


class _Persistence:
    def __init__(self) -> None:
        self._pending: set[str] = set()

    def schedule_save(self, name: str) -> None:
        self._pending.add(name)


def _fake_app():
    from review.core.app_state import AppState

    app = SimpleNamespace()
    app._app_state = AppState()
    app._app_state.dataset.well_paths = {"A01": object()}
    app._persistence = _Persistence()
    app._invalidate_stats_cache = lambda: None
    return app


def test_typing_updates_memory_but_does_not_save_until_commit(qtbot):
    app = _fake_app()
    grid = LabelGrid(app)
    qtbot.addWidget(grid)
    grid.setEnabledWells(["A01"])
    cell = grid._cells["A01"]

    # Typing: textChanged updates the in-memory label; textEdited flags a real
    # user edit. Neither writes to disk.
    cell.setText("Ctrl")            # fires textChanged -> memory update
    cell.textEdited.emit("Ctrl")    # the user-edit signal sets the dirty flag
    assert app._app_state.dataset.well_labels.get("A01") == "Ctrl"
    assert "sample_definitions" not in app._persistence._pending

    # Commit (Enter / focus-out) persists exactly once.
    cell.editingFinished.emit()
    assert "sample_definitions" in app._persistence._pending


def test_commit_without_an_edit_does_not_save(qtbot):
    app = _fake_app()
    grid = LabelGrid(app)
    qtbot.addWidget(grid)
    grid.setEnabledWells(["A01"])
    cell = grid._cells["A01"]

    # Focus-out with no edit (dirty flag never set) must not write.
    cell.editingFinished.emit()
    assert "sample_definitions" not in app._persistence._pending
