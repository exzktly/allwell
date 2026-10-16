"""Runs a Concatenate Datasets merge off the UI thread.

A thin ``QThread`` wrapper: planning (:func:`build_merge_plan`) and execution
(:func:`~datasets.concatenate.apply.execute_plan`) both run inside :meth:`run`,
and their progress / result / errors are marshalled back to the UI through queued
signals — the only safe way to reach the UI thread from a worker, which has no
event loop of its own (CLAUDE.md §5). All the actual file-merging logic lives in
the Qt-free :mod:`~datasets.concatenate.apply` module so it can be unit-tested
without a Qt event loop.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from util.aw_safe import swallowed
from datasets.concatenate.apply import execute_plan
from datasets.concatenate.concatenate_compute import (
    ConcatError, build_merge_plan, parse_gap_hours,
)
from datasets.concatenate.in_place import append_in_place, build_append_plan, recover_dataset


class ConcatenateWorker(QThread):
    """Plans and executes a concatenation, streaming progress to the UI."""

    logLine = Signal(str)          # one line for the status window
    wellsPlanned = Signal(list)    # the wells this merge will actually touch
    wellStage = Signal(str, str)   # (well, "merging"|"merged"|"warned")
    # (well, done, total) — sub-progress *within* one well, so its plate cell
    # can render as a filling dial instead of a flat "merging" until the whole
    # well lands. See datasets.core.containers.WellFraction.
    wellFraction = Signal(str, int, int)
    succeeded = Signal(str, str)   # (destination root, summary line)
    failed = Signal(str)           # user-facing error message

    def __init__(self, ds1_root: str, ds2_root: str, dest_root: str,
                 gap_text: str = "", parent=None, *, in_place: bool = False) -> None:
        super().__init__(parent)
        self._ds1_root = ds1_root
        self._ds2_root = ds2_root
        self._dest_root = dest_root
        self._gap_text = gap_text        # "" → the pre-filled default
        # Append dataset 2 to dataset 1's own archives instead of writing a new
        # dataset (datasets.concatenate.in_place); the destination is unused.
        self._in_place = in_place

    def run(self) -> None:  # noqa: D401 — QThread entry point
        try:
            # Either dataset may hold an in-place append a crash left unfinished;
            # it is undone before anything reads the archives.
            for root in (self._ds1_root, self._ds2_root):
                recover_dataset(root, self.logLine.emit)
            self.logLine.emit("Validating datasets…")
            gap = parse_gap_hours(self._gap_text) if self._gap_text.strip() else None
            if self._in_place:
                plan = build_append_plan(self._ds1_root, self._ds2_root, gap_hours=gap)
            elif not self._dest_root.strip():
                # Path("") is the working directory: a new dataset needs a folder named.
                raise ConcatError("Choose a destination folder for the new dataset.")
            else:
                plan = build_merge_plan(self._ds1_root, self._ds2_root, self._dest_root,
                                        gap_hours=gap)
        except ConcatError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("concatenate planning")
            self.failed.emit(f"Unexpected error while planning: {exc}")
            return

        # In place, only the wells dataset 2 adds to are touched.
        touched = set(plan.wells_ds2_only) | set(plan.wells_both)
        self.wellsPlanned.emit(sorted(touched if plan.in_place else touched | set(plan.wells_ds1_only)))

        try:
            summary = (append_in_place if plan.in_place else execute_plan)(
                plan, self.logLine.emit, self.isInterruptionRequested,
                progress=self.wellStage.emit,
                sub_progress=self.wellFraction.emit,
            )
        except ConcatError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("concatenate execute")
            self.failed.emit(f"Concatenation failed: {exc}")
        else:
            self.succeeded.emit(str(plan.dest.root), summary)


class UndoAppendWorker(QThread):
    """Undoes an unfinished in-place addition off the UI thread.

    *mode* is ``"undo"`` (an ordinary recovery), ``"force"`` (the user
    confirmed that the recorded run is not still going) or ``"keep"`` (the user
    chose to keep the archives as they are). ``done`` carries ``("ok",
    Recovery)``, or ``("refused", message)`` when the undo was refused as
    unsafe or could not complete.
    """

    done = Signal(str, object)

    def __init__(self, root: str, mode: str, parent=None) -> None:
        super().__init__(parent)
        self._root = root
        self._mode = mode

    def run(self) -> None:  # noqa: D401 — QThread entry point
        from pathlib import Path

        from util.zip_append import AppendJournalError, recover

        try:
            result = recover(Path(self._root) / "in", force=self._mode == "force",
                             keep=self._mode == "keep")
        except (AppendJournalError, OSError) as exc:
            self.done.emit("refused", str(exc))
        else:
            self.done.emit("ok", result)
