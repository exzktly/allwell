"""The Analyze form's **Advanced** section: output, performance, scratch folder.

Three groups of settings that a run needs an answer for and that almost nobody
changes, gathered into one folded section at the foot of the form so the
sections above it are the questions every run has to answer. They were three
sections of their own (Output options, Compute options, Scratch disk) — and
Compute options also held which wells to reprocess, which is a decision about
the run rather than about the machine and now lives in Run scope
(``analyze.well_scope``).

Mixed into ``AnalyzeTabFormMixin``; every widget lands on the ``AnalyzeTab``
under the name the rest of the pane reads (``_csv_prefix_edit``,
``_run_check_cb``, ``_tf_threads_edit``, ``_workers_edit``,
``_stardist_tile_px_edit``, ``_cpu_only_cb``, ``_allow_low_disk_cb``,
``_scratch_edit``), which ``analyze/pinfo.py`` restores by name.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLineEdit, QPushButton, QSizePolicy, QWidget,
)

from analyze.storage_options import build_storage_rows
from util.state_dir import default_scratch_label
from widgets.wrap_checkbox import WrapCheckBox

SCRATCH_HELP = (
    "Where per-well working files (zip extraction and intermediate images) are "
    "written during a run. Blank uses {default}, the scratch folder in the "
    "app's own folder. Keep it on fast local storage: on a network drive a "
    "dropped session makes wells fail with 'Bad file descriptor'.")

#: The form's short version of ``pipeline.capacity.ALLOW_LOW_DISK_HELP``, which
#: is also the command line's --help text and says the same at length.
LOW_DISK_TIP = (
    "Start even when the disk check estimates the run will not fit. The "
    "estimate sizes from the largest image and gives no credit for "
    "compression, so it can refuse a run that would finish. When space really "
    "is tight, a roomier scratch folder or fewer wells in parallel is the fix.")

TILE_HELP = (
    "The largest tile side, in pixels, StarDist predicts on; a larger frame is "
    "split into tiles and stitched. 0 predicts the whole frame at once. An "
    "untiled ~4K frame can use several GB per worker, so lower this when "
    "parallel workers run out of memory (2048 splits a 4096-px frame 2×2).")


class AnalyzeAdvancedSectionMixin:
    """The Advanced section's builders. Binds to the ``AnalyzeTab``."""

    def _build_advanced_section(self, parent: QWidget) -> None:
        sec = self._section(
            parent, "Advanced", collapsible=True, collapsed=True,
            summary=lambda: self._changed_summary("Advanced"),
            info="Output naming and storage, how much of the machine a run "
                 "uses, and where it keeps its working files. Inputs and "
                 "outputs are always per-well .zip archives.")
        self._build_output_rows(sec)
        self._build_performance_rows(sec)
        self._build_scratch_rows(sec)

    # ── output ──────────────────────────────────────────────────────────────
    def _build_output_rows(self, sec: QWidget) -> None:
        self._subheading(sec, "Output")
        r = self._row(sec, "CSV prefix")
        self._csv_prefix_edit = self._entry(r, "measurements", width=160)

        # On by default. The check reads back what the run wrote and records
        # whether each well's output covers its input, which is what the Review
        # Home page reports — without it a dataset carries no record of its own
        # completeness and a partly-failed well is indistinguishable from a
        # whole one on disk.
        self._run_check_cb = WrapCheckBox(
            "Check completeness when the run finishes", sec)
        self._run_check_cb.setChecked(True)
        self._run_check_cb.setToolTip(
            "Compare what each well's input held with what its CSV and output "
            "archive cover, and record the result with the dataset. Takes "
            "seconds. Off, the dataset carries no completeness record.")
        sec.layout().addWidget(self._run_check_cb)
        build_storage_rows(self, sec)
        self._refresh_storage_rows()      # the phase box shows only with a Phase channel

    # ── performance ─────────────────────────────────────────────────────────
    def _build_performance_rows(self, sec: QWidget) -> None:
        self._subheading(sec, "Performance")
        tf_row = self._row(
            sec, "Threads per worker",
            tooltip="TensorFlow threads each worker may use. 0 picks 4 (2 on a "
                    "Mac using Metal), capped at the cores available.")
        self._tf_threads_edit = self._entry(tf_row, "0", width=40)
        self._field_note(tf_row, "0 = auto")
        tf_row.layout().addStretch(1)

        w_row = self._row(
            sec, "Wells in parallel",
            tooltip="How many worker processes run at once, each on one well. "
                    "0 divides the available cores by the threads per worker.")
        self._workers_edit = self._entry(w_row, "0", width=40)
        self._field_note(w_row, "0 = auto")
        w_row.layout().addStretch(1)

        # StarDist prediction tiling — caps per-worker peak RAM on large frames.
        # The value is the MAX pixels per tile SIDE (not the number of tiles).
        tile_row = self._row(sec, "Max tile size", tooltip=TILE_HELP)
        self._stardist_tile_px_edit = self._entry(tile_row, "0", width=40)
        self._field_note(tile_row, "px · 0 = off")
        tile_row.layout().addStretch(1)

        self._cpu_only_cb = QCheckBox("CPU only (disable GPU)", sec)
        sec.layout().addWidget(self._cpu_only_cb)
        self._allow_low_disk_cb = QCheckBox(
            "Start even if the disk looks too small", sec)
        self._allow_low_disk_cb.setToolTip(LOW_DISK_TIP)
        sec.layout().addWidget(self._allow_low_disk_cb)

    # ── scratch ─────────────────────────────────────────────────────────────
    def _build_scratch_rows(self, sec: QWidget) -> None:
        """The scratch-folder picker.

        Per-well working files (zip extraction + intermediate images + FOV
        checkpoints) are written here. It defaults to ``scratch`` in the app's
        folder — ``~/.allwell/scratch``, or ``%LOCALAPPDATA%\\allwell\\scratch``
        on Windows (``pipeline.io.scratch_base_for``) — and **does not track the
        input folder**: seeding it with ``<input>/Temp`` put the run's whole
        GB-scale temporary I/O on the input volume, which is the configuration
        behind "Bad file descriptor" well failures when that volume is a network
        mount — a dropped SMB session invalidates every open handle.
        """
        help_text = SCRATCH_HELP.format(default=default_scratch_label())
        self._subheading(sec, "Scratch folder", info=help_text)
        pick = QWidget(sec)
        pl = QHBoxLayout(pick)
        pl.setContentsMargins(0, 0, 0, 4)
        pl.setSpacing(4)
        self._scratch_edit = QLineEdit(pick)
        self._scratch_edit.setPlaceholderText(f"(defaults to {default_scratch_label()})")
        self._scratch_edit.setMinimumWidth(0)
        self._scratch_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        pl.addWidget(self._scratch_edit, 1)
        self._scratch_browse_btn = QPushButton("Browse…", pick)
        self._scratch_browse_btn.setProperty("variant", "secondary")
        self._scratch_browse_btn.clicked.connect(self._browse_scratch)
        pl.addWidget(self._scratch_browse_btn)
        sec.layout().addWidget(pick)
        # Typing / browsing pins the value; blank means the pipeline default.
        self._scratch_user_set = False
        self._scratch_edit.textEdited.connect(self._on_scratch_edited)

    def _on_scratch_edited(self, _text: str = "") -> None:
        self._scratch_user_set = True

    def _browse_scratch(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Select scratch folder", "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if d:
            self._scratch_edit.setText(d)
            self._scratch_user_set = True
