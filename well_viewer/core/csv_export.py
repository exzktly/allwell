"""Writing a plain CSV the user picked a path for.

In ``core/`` rather than in ``features/export/`` because it knows nothing about
plots, batches or metrics — it is a file dialog, a writer and one status line.
Leaving it in the export feature meant any other feature that wanted to save a
table had to import that feature at module scope, which is a cross-feature
dependency, or defer the import, which is a cycle workaround for a cycle that
does not exist. ``tests/test_deferred_imports.py`` is what noticed.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

_logger = logging.getLogger("well_viewer")

_CSV_FILTER = "CSV files (*.csv);;All files (*)"


def ask_save_csv(ctx, title: str, default_name: str) -> str:
    """A save-path dialog rooted at the open dataset. ``""`` if cancelled."""
    data_dir = ctx.state.dataset.data_dir
    initial = str(Path(str(data_dir)) / default_name) if data_dir else default_name
    out, _ = QFileDialog.getSaveFileName(ctx.window, title, initial, _CSV_FILTER)
    return out or ""


def write_csv_rows(ctx, default_stem: str, header, rows) -> bool:
    """Ask for a path, write *header* + *rows*, and report. ``False`` if cancelled.

    The open / write / status / OSError block was written out four times in the
    export service before this existed, each with its own wording for the same
    outcome. Callers holding plain lists use this; the dict-writer paths there
    keep their own, because their schemas are built from field names.
    """
    out_path = ask_save_csv(ctx, "Export CSV", f"{default_stem}.csv")
    if not out_path:
        return False
    try:
        with open(out_path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(list(header))
            writer.writerows([list(r) for r in rows])
    except OSError as exc:
        QMessageBox.critical(ctx.window, "Export failed", str(exc))
        return False
    ctx.notify.set_status(
        f"Exported {len(rows)} row(s) to {Path(out_path).name}")
    return True
