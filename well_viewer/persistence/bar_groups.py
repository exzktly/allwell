"""Bar/replicate-group persistence (user-picked JSON file).

Reads/writes the same unified ``selections`` model as ``pipeline_info.json``
(schema v2: ``{"schema_version": 2, "selections": [...], "current_id": ...}``).
A legacy ``{"rep_sets": [...], "groups": [...]}`` file is migrated on load.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from well_viewer.domain import selections_model as _sel
from well_viewer.persistence._io import atomic_write_json

_logger = logging.getLogger("well_viewer")


def to_payload(app) -> dict:
    """Serialise the app's unified ``selections`` state for ``bar_groups.json``."""
    return _sel.to_bar_groups_payload(
        list(app._app_state.selection.selections or []),
        app._app_state.selection.current_selection_id,
    )


def from_dict(app, data) -> None:
    """Restore selections state on *app* from a saved (v1 or v2) payload.
    v1 is migrated by ``from_block``."""
    tok_to_label = app._app_state.dataset.tok_to_label
    selections, current_id, _labels, _notes, _plot_groups = _sel.from_block(
        data, tok_to_label=tok_to_label,
    )
    app._app_state.selection.selections = selections
    app._app_state.selection.current_selection_id = current_id


def save_via_dialog(app) -> None:
    """Prompt the user for a JSON path and write the current selections."""
    if not app._app_state.selection.selections:
        QMessageBox.warning(
            app, "Nothing to save",
            "Define at least one selection before saving.",
        )
        return
    out_dir = app._app_state.dataset.data_dir if app._app_state.dataset.data_dir else None
    init_dir = str(out_dir) if out_dir else ""
    init_path = str(Path(init_dir) / "bar_groups.json") if init_dir else "bar_groups.json"
    path_str, _ = QFileDialog.getSaveFileName(
        app, "Save bar group definitions",
        init_path,
        "Group definitions JSON (*.json);;All files (*.*)",
    )
    if not path_str:
        return
    try:
        atomic_write_json(Path(path_str), to_payload(app))
        _logger.info("Bar groups saved to %s", path_str)
    except OSError as exc:
        QMessageBox.critical(app, "Save failed", str(exc))


def load_via_dialog(app) -> None:
    """Prompt the user for a JSON path and load bar groups from it."""
    out_dir = app._app_state.dataset.data_dir if app._app_state.dataset.data_dir else None
    init_dir = str(out_dir) if out_dir else ""
    path_str, _ = QFileDialog.getOpenFileName(
        app, "Load bar group definitions",
        init_dir,
        "Group definitions JSON (*.json);;All files (*.*)",
    )
    if not path_str:
        return
    try:
        with open(path_str, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object at the top level.")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        QMessageBox.critical(
            app, "Load failed",
            f"Could not read group definitions:\n{exc}",
        )
        return
    if app._app_state.selection.selections:
        resp = QMessageBox.question(
            app, "Replace existing groups?",
            f"Loading will replace the current {len(app._app_state.selection.selections)} "
            f"group(s).  Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
    from_dict(app, data)
    app._rebuild_all()
    _logger.info("Bar groups loaded from %s (%d group(s))",
                 path_str, len(app._app_state.selection.selections))
