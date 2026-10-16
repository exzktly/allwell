"""Analyze-tab form construction (Qt widget builders), split from analyze/tab.py.

``AnalyzeTab`` mixes in :class:`AnalyzeTabFormMixin` to inherit the ``_build_*``
section builders verbatim — they still bind to ``self`` exactly as before, so
this is a pure relocation (no behaviour change), trimming the 1.8k-line god-class
down to its run / validation / log-poll core. The schema vocabulary constants
live here too (the form reads them) and are re-exported by ``analyze.tab``.

Restoring a prior run's settings from its ``pipeline_info.json`` sidecar is its
own cohesive job and lives in ``analyze/pinfo.py``; the form's own chrome
(``_section``/``_row``/``_entry``/``_info_icon``/``reveal_section``) lives in
``analyze/form_chrome.py``; the per-method segmentation params, thresholds and
hints in ``analyze/seg_params.py``; the Quality Control section in
``analyze/qc_section.py``. This mixin inherits them all, so every
name stays reachable on the form exactly where callers expect it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from analyze.form_chrome import AnalyzeFormChromeMixin
from analyze.storage_options import build_storage_rows, storage_options
from analyze.well_scope import build_well_scope_rows, well_scope_options
from analyze.domain.fields import (  # noqa: F401 — re-exported for analyze.tab
    _INT_FIELDS,
    _numeric_option_errors,
)
from analyze.pinfo import AnalyzePipelineInfoMixin
from analyze.nanowell_section import AnalyzeNanowellSectionMixin
from analyze.qc_section import AnalyzeQCSectionMixin
from analyze.schema_section import AnalyzeSchemaSectionMixin
from analyze.seg_params import AnalyzeSegParamsMixin
from analyze.slurm_job_view import SlurmJobIndicator
from services.remote_config import default_remote_index, load_remotes
from widgets.checkable_dropdown import CheckableDropdown
from widgets.wrap_checkbox import WrapCheckBox
import pipeline.shape_features as _sfc
from pipeline.capacity import ALLOW_LOW_DISK_HELP

# ---------------------------------------------------------------------------
# Schema vocabulary — shared with process_microscopy and well_plate_zipper via
# util.schema, so the Analyze form's field picker offers exactly the tokens the
# pipeline parses. This module previously kept its own copy "in sync with
# process_microscopy.py" by hand, which is the arrangement util.schema exists
# to replace. The picker widget itself is shared with the Datasets pane's Zip
# Loose Folder page — see widgets/schema_picker.py.
# ---------------------------------------------------------------------------
from util.schema import SCHEMA_FIELDS, DEFAULT_SCHEMA, DEFAULT_SEP  # noqa: F401 (re-export)
from util.well_token import canonical_well_label, is_valid_well_name


class AnalyzeTabFormMixin(AnalyzeFormChromeMixin, AnalyzeNanowellSectionMixin,
                          AnalyzePipelineInfoMixin, AnalyzeQCSectionMixin,
                          AnalyzeSchemaSectionMixin, AnalyzeSegParamsMixin):
    """The Analyze-tab Qt form builders. Mixed into ``AnalyzeTab`` (with
    ``QWidget``); every method binds to the ``AnalyzeTab`` instance ``self``."""

    def _build_form(self, parent: QWidget) -> None:
        self._build_banner_strips(parent)
        self._build_open_pinfo_row(parent)
        self._build_schema_section(parent)
        # Folder first, then what is in it; Schema leads both (see its hint).
        self._build_folders_section(parent)
        self._build_channel_tokens_section(parent)
        self._refresh_schema_preview()
        self._build_nanowell_section(parent)
        # Quality Control leads the optional sections and stays open: its
        # correction stages are common tuning knobs, not an afterthought.
        self._build_qc_section(parent)
        self._build_auto_threshold_section(parent)
        self._build_shape_features_section(parent)
        self._build_output_options_section(parent)
        self._build_compute_options_section(parent)
        self._build_scratch_section(parent)
        self._build_run_controls(parent)
        parent.layout().addStretch(1)

    def _build_channel_tokens_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Channel Tokens")

        seg_row = self._row(sec, "Segmentation")
        self._seg_method_cb = QComboBox(seg_row)
        # Cellpose is a removable, torch-backed segmenter (profiles/full.toml). A
        # profile that doesn't list "Cellpose" (e.g. the StarDist-only thin build)
        # ships no torch/cellpose, so its methods can't run — drop them from the
        # picker instead of offering a choice that would fail at pipeline start.
        from core.profile import active_profile
        _prof = active_profile()
        _cellpose_ok = _prof.features is None or "Cellpose" in _prof.features
        for _value, _label in self._SEG_METHODS:
            if not _cellpose_ok and _value.startswith("cellpose"):
                continue
            self._seg_method_cb.addItem(_label, userData=_value)
        # Narrower than the longest item ("Label-free (Cellpose, brightfield)")
        # — Qt elides the closed box's text; the tooltip carries the full label.
        self._seg_method_cb.setFixedWidth(self._SEG_METHOD_CB_W)
        self._seg_method_cb.currentIndexChanged.connect(
            lambda _i: self._seg_method_cb.setToolTip(self._seg_method_cb.currentText()))
        self._seg_method_cb.setToolTip(self._seg_method_cb.currentText())
        seg_row.layout().addWidget(self._seg_method_cb)
        seg_row.layout().addStretch(1)
        self._seg_method_cb.currentIndexChanged.connect(lambda _: self._refresh_segmentation_hints())

        nuc_row = self._row(sec, "Segmentation channel")
        self._nuclear_row_widget = nuc_row
        self._nuclear_token_edit = self._entry(nuc_row, "NIR")
        nuc_row.layout().addStretch(1)
        self._nuclear_token_edit.textChanged.connect(lambda _: self._refresh_schema_preview())
        self._nuclear_token_edit.textChanged.connect(lambda _: self._refresh_segmentation_hints())

        fl_header = QWidget(sec)
        fl_hl = QHBoxLayout(fl_header)
        fl_hl.setContentsMargins(0, 6, 0, 2)
        fl_hl.setSpacing(4)
        fl_title = QLabel("Fluorescent channels", fl_header)
        fl_hl.addWidget(fl_title)
        fl_hl.addWidget(self._info_icon(
            fl_header,
            "Mark smFISH channels, or one channel as Phase for label-free "
            "segmentation, with the checkboxes on each row.",
        ))
        fl_hl.addStretch(1)
        sec.layout().addWidget(fl_header)
        self._fluor_header = fl_header  # for an OME-mode hide nothing performs yet

        self._fluor_frame = QWidget(sec)
        self._fluor_frame_layout = QVBoxLayout(self._fluor_frame)
        self._fluor_frame_layout.setContentsMargins(0, 0, 0, 0)
        self._fluor_frame_layout.setSpacing(2)
        sec.layout().addWidget(self._fluor_frame)
        self._fluor_add_row("GFP")

        add_btn = QPushButton("+ Add channel", sec)
        add_btn.setProperty("variant", "secondary")
        add_btn.setFixedWidth(self._ADD_BTN_W)
        add_btn.clicked.connect(lambda _=False: self._fluor_add_row())
        sec.layout().addWidget(add_btn)
        self._fluor_add_btn = add_btn  # for an OME-mode hide nothing performs yet

        # Per-method parameter pages + the shared StarDist thresholds
        # (analyze.seg_params.AnalyzeSegParamsMixin).
        self._build_seg_param_stack(sec)

        self._segmentation_hint_lbl = QLabel("", sec)
        self._segmentation_hint_lbl.setObjectName("Muted")
        self._segmentation_hint_lbl.setWordWrap(True)
        sec.layout().addWidget(self._segmentation_hint_lbl)
        self._refresh_segmentation_hints()

    def _build_open_pinfo_row(self, parent: QWidget) -> None:
        """Top-of-tab button: restore a prior run's settings (incl. input/output
        paths) from its pipeline_info.json sidecar — to re-run a failed run or
        append new data."""
        row = QWidget(parent)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(12, 8, 12, 0)
        rl.setSpacing(4)
        self._open_pinfo_btn = QPushButton("Load settings from a run…", row)
        self._open_pinfo_btn.setProperty("variant", "secondary")
        self._open_pinfo_btn.clicked.connect(self._open_pipeline_info)
        rl.addWidget(self._open_pinfo_btn)
        rl.addStretch(1)
        parent.layout().addWidget(row)

    def _build_folders_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Folders")

        in_lbl = QLabel("Input folder", sec)
        in_lbl.setWordWrap(True)
        sec.layout().addWidget(in_lbl)

        in_pick = QWidget(sec)
        ipl = QHBoxLayout(in_pick)
        ipl.setContentsMargins(0, 0, 0, 4)
        ipl.setSpacing(4)
        self._input_edit = QLineEdit(in_pick)
        self._input_edit.setReadOnly(True)
        self._input_edit.setPlaceholderText("(not set)")
        self._input_edit.setEnabled(False)
        self._input_edit.setMinimumWidth(0)
        self._input_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        ipl.addWidget(self._input_edit, 1)
        self._browse_btn = QPushButton("Browse…", in_pick)
        self._browse_btn.setProperty("variant", "secondary")
        self._browse_btn.setEnabled(False)
        self._browse_btn.clicked.connect(self._browse_input)
        ipl.addWidget(self._browse_btn)
        # Balancing stretch: input field gets ~50% of the row width
        # rather than stretching to fill the whole sidebar.
        ipl.addStretch(1)
        sec.layout().addWidget(in_pick)

        self._folder_lock_lbl = QLabel("Define the filename schema above first.", sec)
        self._folder_lock_lbl.setObjectName("Muted")
        self._folder_lock_lbl.setWordWrap(True)
        sec.layout().addWidget(self._folder_lock_lbl)

        out_lbl = QLabel("Output folder (auto)", sec)
        out_lbl.setContentsMargins(0, 6, 0, 0)
        out_lbl.setWordWrap(True)
        sec.layout().addWidget(out_lbl)
        self._output_lbl = QLabel("—", sec)
        self._output_lbl.setObjectName("Muted")
        self._output_lbl.setWordWrap(True)
        sec.layout().addWidget(self._output_lbl)

        self._layout_lbl = QLabel("", sec)
        self._layout_lbl.setWordWrap(True)
        sec.layout().addWidget(self._layout_lbl)

        self._input_edit.textChanged.connect(lambda _: self._refresh_output())

    def _build_shape_features_section(self, parent: QWidget) -> None:
        sec = self._section(
            parent, "Shape Metrics",
            collapsible=True, collapsed=True,
            info="Per-cell morphology features to measure (emitted as shape_… "
                 "columns; cell centroids are always recorded).",
        )

        # A compact checkbox dropdown rather than a wide chip grid — the
        # selectable-feature list is long enough to overflow the narrow
        # Analyze sidebar. ``checkedData()`` / ``setCheckedData()`` keep the
        # same contract the chip group exposed, so _collect_run_options needs
        # no change.
        self._shape_chips = CheckableDropdown(sec, noun="metrics selected",
                                              empty_text="No metrics selected")
        for key in _sfc.SELECTABLE_KEYS:
            feat = _sfc.BY_KEY[key]
            self._shape_chips.addItem(feat.label, data=key)
        self._shape_chips.setCheckedData(list(self._SHAPE_DEFAULTS))
        # Constrain width to match the other controls (e.g. the segmentation
        # combo's 230 px). Added directly to the section's QVBoxLayout the dropdown
        # button's Preferred policy stretched it to the full ~414 px sidebar, which
        # looked oversized; cap it and left-align with a trailing stretch.
        self._shape_chips.setMaximumWidth(self._SHAPE_CHIPS_MAX_W)
        chip_row = QWidget(sec)
        crl = QHBoxLayout(chip_row)
        crl.setContentsMargins(0, 0, 0, 0)
        crl.setSpacing(4)
        crl.addWidget(self._shape_chips)
        crl.addStretch(1)
        sec.layout().addWidget(chip_row)

    def _build_output_options_section(self, parent: QWidget) -> None:
        sec = self._section(
            parent, "Output Options",
            collapsible=True, collapsed=True,
            info="Inputs are always grouped into per-well .zip files and outputs "
                 "are always written as per-well .zip files.",
        )

        r = self._row(sec, "CSV prefix")
        self._csv_prefix_edit = self._entry(r, "measurements", width=160)
        r.layout().addStretch(1)

        # On by default. The check reads back what the run wrote and records
        # whether each well's output covers its input, which is what the Review
        # Home page reports — without it a dataset carries no record of its own
        # completeness and a partly-failed well is indistinguishable from a
        # whole one on disk.
        self._run_check_cb = WrapCheckBox(
            "Check completeness when the run finishes", sec)
        self._run_check_cb.setChecked(True)
        self._run_check_cb.setToolTip(
            "After the last well, compare what each well's input held against "
            "what its CSV and output archive actually cover, and record the "
            "result in pipeline_info.json. Costs seconds — the files were just "
            "written, so they are still in the page cache. Turning it off "
            "leaves the dataset with no completeness record; Review then says "
            "the check was turned off rather than guessing.")
        sec.layout().addWidget(self._run_check_cb)
        build_storage_rows(self, sec)

    def _build_compute_options_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Compute Options", collapsible=True, collapsed=True)

        # Hint below its row, not inline — matches the Mesh tile/sigma note below.
        def _hint(text: str) -> None:
            lbl = QLabel(text, sec)
            lbl.setObjectName("Muted")
            lbl.setWordWrap(True)
            sec.layout().addWidget(lbl)

        tf_row = self._row(sec, "TF threads (0=auto)")
        self._tf_threads_edit = self._entry(tf_row, "0", width=40)
        tf_row.layout().addStretch(1)
        _hint("(0 → auto-select 4)")

        w_row = self._row(sec, "Workers (0=auto)")
        self._workers_edit = self._entry(w_row, "0", width=40)
        w_row.layout().addStretch(1)
        _hint("(process count override)")

        # StarDist prediction tiling — caps per-worker peak RAM on large frames
        # (an untiled ~4K predict can use several GB/worker, so parallel workers
        # exhaust memory). The value is the MAX pixels per tile SIDE (not the
        # number of tiles), 0 = off (default). Label kept short so it fits the
        # row label column; the px/side meaning is in the info-icon tooltip.
        tile_row = self._row(sec, "Max tile size (0=off)")
        # Same width as the TF threads / Workers entries above it — these three
        # small integer fields read as one uniform column.
        self._stardist_tile_px_edit = self._entry(tile_row, "0", width=40)
        tile_row.layout().addWidget(self._info_icon(
            tile_row,
            "Max pixels per tile SIDE for StarDist prediction — NOT the number "
            "of tiles. StarDist splits the frame into as many tiles as needed so "
            "no block exceeds this size, then stitches the result. 0 = off "
            "(predict the whole frame at once). On large frames (~4K) an untiled "
            "predict can use several GB per worker, so a few parallel workers "
            "exhaust RAM; e.g. 2048 splits a 4096-px frame into a 2×2 grid, 1024 "
            "into 4×4. Smaller = less RAM per worker, slightly slower."))
        tile_row.layout().addStretch(1)

        self._cpu_only_cb = QCheckBox("CPU only (disable GPU)", sec)
        sec.layout().addWidget(self._cpu_only_cb)
        self._allow_low_disk_cb = QCheckBox(
            "Start even if the disk looks too small", sec)
        self._allow_low_disk_cb.setToolTip(ALLOW_LOW_DISK_HELP)
        sec.layout().addWidget(self._allow_low_disk_cb)
        self._force_cb = QCheckBox("Force reprocess all wells", sec)
        self._force_cb.setToolTip(
            "Reprocess every well from scratch, even one whose output already "
            "exists. Overrides the incremental option below — the pipeline "
            "ignores --incremental when --force is given.")
        sec.layout().addWidget(self._force_cb)
        # The run mode that finishes a partial run. Without it, a well whose
        # <well>_out.zip and CSV both exist is SKIPPED whole, however few of its
        # FOV-groups actually made it in — which is why a crashed run cannot be
        # completed by simply pressing Run again. Datasets → Validate & Repair's
        # "Complete this run in Analyze" ticks this on the way in.
        self._incremental_cb = QCheckBox("Incremental — process only missing FOVs", sec)
        self._incremental_cb.setToolTip(
            "Fill in the gaps a partial or crashed run left, instead of "
            "skipping any well that already has output. Each well's already-"
            "recorded (FOV, timepoint) pairs are read from the output folder "
            "and only the missing ones are processed, then merged into the "
            "existing per-well CSV and <well>_out.zip. Wells with no output at "
            "all process in full; wells that are already complete are left "
            "untouched.")
        sec.layout().addWidget(self._incremental_cb)
        # Per-well scope — Only these wells / Force these wells — as visible
        # fields; see analyze/well_scope.py.
        build_well_scope_rows(self, sec)

    def _build_scratch_section(self, parent: QWidget) -> None:
        """Scratch-disk picker — the very last option before the Run controls.

        Per-well working files (zip extraction + intermediate images + FOV
        checkpoints) are written here. It defaults to ``~/.allwell/scratch``
        (``pipeline.io.scratch_base_for``) and **no longer tracks the input
        folder**: seeding it with ``<input>/Temp`` put the run's whole GB-scale
        temporary I/O on the input volume, which is the configuration behind
        "Bad file descriptor" well failures when that volume is a network mount
        — a dropped SMB session invalidates every open handle. Keep this on
        local storage.
        """
        sec = self._section(
            parent, "Scratch Disk",
            collapsible=True, collapsed=True,
            info="Where per-well working files (zip extraction + intermediate "
                 "images) are written during a run. Leave blank to use "
                 "~/.allwell/scratch. Keep it on fast LOCAL storage — pointing "
                 "it at a network drive (NAS / SMB share) puts the run's "
                 "GB-scale temporary I/O on a volume whose dropped sessions "
                 "make wells fail with 'Bad file descriptor'.",
        )
        pick = QWidget(sec)
        pl = QHBoxLayout(pick)
        pl.setContentsMargins(0, 0, 0, 4)
        pl.setSpacing(4)
        self._scratch_edit = QLineEdit(pick)
        self._scratch_edit.setPlaceholderText("(defaults to ~/.allwell/scratch)")
        self._scratch_edit.setMinimumWidth(0)
        self._scratch_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        pl.addWidget(self._scratch_edit, 1)
        self._scratch_browse_btn = QPushButton("Browse…", pick)
        self._scratch_browse_btn.setProperty("variant", "secondary")
        self._scratch_browse_btn.clicked.connect(self._browse_scratch)
        pl.addWidget(self._scratch_browse_btn)
        pl.addStretch(1)
        sec.layout().addWidget(pick)
        # Typing / browsing pins the value; blank means the pipeline default
        # (~/.allwell/scratch). The field no longer follows the input dir — see
        # the docstring above for why that default was harmful.
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

    def _build_run_controls(self, parent: QWidget) -> None:
        # Remote-execution selector. Populated from ~/.config/allwell/remotes.toml
        # so users can flip between Local and any configured SSH host
        # without restarting the GUI.
        remote_widget = QWidget(parent)
        rl = QHBoxLayout(remote_widget)
        rl.setContentsMargins(12, 6, 12, 0)
        rl.setSpacing(8)
        rl.addWidget(QLabel("Run on:", remote_widget))
        self._remote_cb = QComboBox(remote_widget)
        self._remote_cb.addItem("Local", userData=None)
        try:
            for name in load_remotes():
                self._remote_cb.addItem(name, userData=name)
        except (ValueError, OSError) as exc:
            # Malformed remotes.toml shouldn't bring the GUI down — just
            # surface the error in the log and stick with Local.
            self._log_q.put((
                "line",
                f"[warn] could not load remotes.toml: {exc}\n",
            ))
        # Default to the first configured remote when any exist (else Local).
        self._remote_cb.setCurrentIndex(
            default_remote_index(self._remote_cb.count()))
        rl.addWidget(self._remote_cb)
        self._manage_remotes_btn = QPushButton("Manage…", remote_widget)
        self._manage_remotes_btn.setProperty("variant", "secondary")
        self._manage_remotes_btn.setToolTip(
            "Add, edit, delete, test, or bootstrap remote-execution hosts."
        )
        self._manage_remotes_btn.clicked.connect(self._open_manage_remotes)
        rl.addWidget(self._manage_remotes_btn)
        # Lit while this run holds an open Slurm job; its tooltip is the node.
        self._slurm_indicator = SlurmJobIndicator(remote_widget)
        rl.addWidget(self._slurm_indicator)
        rl.addStretch(1)
        parent.layout().addWidget(remote_widget)

        # ── Remote-only parameters, grouped in one place ─────────────────────
        # Every knob that only matters for a remote run lives in this container:
        # the batch size (folded in from the former Output-Options section), the
        # auto-delete toggle, and its dry-run companion. The whole group is
        # shown only when a remote is selected (see _refresh_remote_controls),
        # so a Local run isn't cluttered with options that do nothing.
        self._remote_params_widget = QWidget(parent)
        rp = QVBoxLayout(self._remote_params_widget)
        rp.setContentsMargins(12, 4, 12, 0)
        rp.setSpacing(4)

        rp_title = QLabel("Remote options", self._remote_params_widget)
        rp_title.setObjectName("SectionTitle")
        rp.addWidget(rp_title)

        # Batched mode. ``0`` (default) uploads every well-zip up front and runs
        # the pipeline once across the whole plate — lowest total runtime. Any
        # positive integer processes that many wells at a time: peak remote disk
        # ≈ batch_size wells of input + ~1 well of output (per-well Fetcher
        # cleanup), at the cost of one StarDist reload per batch (~5–10 s
        # overhead each). Pick based on the remote's free disk.
        bs_row = self._row(self._remote_params_widget, "Batch size (0=off)")
        self._remote_batch_size_edit = self._entry(bs_row, "0", width=40)
        bs_hint = QLabel("(bounds peak disk to ~N wells of input)", bs_row)
        bs_hint.setObjectName("Muted"); bs_hint.setWordWrap(True)
        bs_row.layout().addWidget(bs_hint, 1)

        # ON by default: a remote that accumulates every run fills up, and a full
        # volume is how a run silently loses FOV-groups. Only deletes after a
        # verified-successful run, never failed work.
        self._cleanup_remote_cb = QCheckBox(
            "Auto-delete remote files when done", self._remote_params_widget)
        self._cleanup_remote_cb.setChecked(True)
        self._cleanup_remote_cb.setToolTip(
            "Checked (default) = after a verified-successful run, delete both the "
            "per-run output dir AND the cached input on the remote, so a shared "
            "machine does not fill up. Unchecked = leave it untouched.")
        rp.addWidget(self._cleanup_remote_cb)

        # Log what auto-delete WOULD remove without removing it. Only enabled
        # when auto-delete is checked.
        self._dry_run_cleanup_cb = WrapCheckBox(
            "Dry-run cleanup (show what would be deleted, don't delete)",
            self._remote_params_widget,
        )
        self._dry_run_cleanup_cb.setToolTip(
            "With auto-delete enabled, log path + file count + total bytes for "
            "each remote directory that WOULD be removed at end-of-run, without "
            "removing anything. Useful for confirming what auto-delete will do "
            "on a shared remote before committing to it."
        )
        rp.addWidget(self._dry_run_cleanup_cb)

        parent.layout().addWidget(self._remote_params_widget)

        # Show the remote-only group only when a remote is selected; dry-run
        # only makes sense when auto-delete is on.
        self._remote_cb.currentIndexChanged.connect(self._refresh_remote_controls)
        self._cleanup_remote_cb.toggled.connect(self._refresh_remote_controls)
        self._refresh_remote_controls()

        parent.layout().addWidget(self._build_run_row(parent))

    def _build_auto_threshold_section(self, parent: QWidget) -> None:
        """Auto-threshold — every threshold-related control in one place.

        Three controls, in the order they apply: the checkbox drives the
        pipeline's ``--no_auto_threshold``; the method combo picks how the cut is
        chosen; the two well fields name the controls the second method compares.

        The method is an explicit choice rather than being inferred from whether
        the wells are filled, so switching back to Otsu does not lose them.
        Collapsed by default, like the other optional sections.
        """
        sec = self._section(
            parent, "Auto-threshold",
            collapsible=True, collapsed=True,
            info="After a run finishes, All-Well can pick a default 'on' "
                 "threshold for each channel and store it with the dataset, so "
                 "the Cell Gating tab opens with sensible values. Otsu (the "
                 "default) splits cell signal from background across the whole "
                 "dataset. Control wells instead take the cut that best "
                 "separates a well you know is ON from one you know is OFF, "
                 "which is usually the better answer because it is anchored to "
                 "biology rather than to pixel statistics. The segmentation "
                 "channel is always set to 0 — it defines cell boundaries and is "
                 "not something to gate on.",
        )

        self._auto_threshold_cb = WrapCheckBox(
            "Calculate cell-gating thresholds when the run finishes", sec,
        )
        self._auto_threshold_cb.setChecked(True)
        self._auto_threshold_cb.setToolTip(
            "On (default): the pipeline writes a default per-channel threshold "
            "into pipeline_info.json at the end of the run. Off: no thresholds "
            "are written and the Cell Gating tab starts from its own defaults."
        )
        sec.layout().addWidget(self._auto_threshold_cb)

        rmethod = self._row(sec, "Method")
        self._auto_threshold_method_cb = QComboBox(rmethod)
        self._auto_threshold_method_cb.addItem("Otsu (whole dataset)", "otsu")
        self._auto_threshold_method_cb.addItem("Positive / negative controls",
                                               "controls")
        # Narrower than "Positive / negative controls" — Qt elides the closed
        # box's text; the tooltip below spells out both options regardless.
        self._auto_threshold_method_cb.setFixedWidth(self._AUTO_THRESHOLD_METHOD_CB_W)
        self._auto_threshold_method_cb.setToolTip(
            "Otsu: split each channel's per-cell signal from image background, "
            "pooled across the dataset — no controls needed, but it cannot tell "
            "which cells are biologically positive.\n"
            "Positive / negative controls: sweep for the cut that best separates "
            "the two named wells."
        )
        rmethod.layout().addWidget(self._auto_threshold_method_cb)
        rmethod.layout().addStretch(1)
        self._auto_threshold_method_row = rmethod.parentWidget()

        rpos = self._row(sec, "Positive control well")
        self._pos_control_well_edit = self._entry(rpos, "", width=160)
        self._pos_control_well_edit.setPlaceholderText("e.g. B02")
        self._pos_control_well_edit.setToolTip(
            "A well where the signal is known to be ON. Optional — but if you "
            "give one you must give the negative control too."
        )
        rpos.layout().addStretch(1)

        rneg = self._row(sec, "Negative control well")
        self._neg_control_well_edit = self._entry(rneg, "", width=160)
        self._neg_control_well_edit.setPlaceholderText("e.g. B11")
        self._neg_control_well_edit.setToolTip(
            "A well where the signal is known to be OFF. The threshold chosen "
            "for each channel is the one that best separates this well from the "
            "positive control."
        )
        rneg.layout().addStretch(1)

        # ``_row`` returns the right-hand cell; the labels live in its parent,
        # so hide the parents or the labels stay behind on their own.
        self._auto_threshold_rows = (rpos.parentWidget(), rneg.parentWidget())

        def _sync_auto_threshold(_=None) -> None:
            on = self._auto_threshold_cb.isChecked()
            self._auto_threshold_method_row.setVisible(on)
            # Text is left alone — switching to Otsu and back must not cost the
            # user their wells.
            wells = on and self._auto_threshold_method() == "controls"
            for row in self._auto_threshold_rows:
                row.setVisible(wells)

        self._auto_threshold_cb.toggled.connect(_sync_auto_threshold)
        self._auto_threshold_method_cb.currentIndexChanged.connect(_sync_auto_threshold)
        _sync_auto_threshold()

    def _auto_threshold_method(self) -> str:
        """``"otsu"`` or ``"controls"`` — the selected estimator."""
        return str(self._auto_threshold_method_cb.currentData() or "otsu")

    def _auto_threshold_validation_error(self, *, check_input: bool = True) -> str | None:
        """Block the run when the control wells are half-given or unusable.

        ``None`` when valid — including the pass being off or Otsu selected,
        neither of which reads the well fields.
        """
        if not self._auto_threshold_cb.isChecked():
            return None
        if self._auto_threshold_method() != "controls":
            return None       # Otsu needs no wells; any text is dormant
        pos = self._pos_control_well_edit.text().strip()
        neg = self._neg_control_well_edit.text().strip()
        if not pos or not neg:
            missing = "positive" if not pos else "negative"
            return (
                f"The control-well method needs both wells — the {missing} "
                f"control well is empty.\n\n"
                "The threshold is chosen from the separation between the two "
                "wells, so one on its own says nothing. Switch Method back to "
                "Otsu if you don't have a pair."
            )
        bad = [w for w in (pos, neg) if not is_valid_well_name(w)]
        if bad:
            label = ", ".join(f"'{w}'" for w in bad)
            return (f"Control well {label} is not a valid 96-well position.\n\n"
                    "Use a row letter A–H followed by a column number 1–12 "
                    "(e.g. B02).")
        if canonical_well_label(pos) == canonical_well_label(neg):
            return ("The positive and negative control wells are the same well "
                    f"('{pos}').\n\nThey have to differ — the threshold comes "
                    "from the separation between them.")
        input_dir = Path(self._input_edit.text().strip())
        sep = self._schema_picker.separator()
        schema = [f.strip().lower() for f in self._build_schema_arg().split(":") if f.strip()]
        from services.input_resolution_service import well_present_in_input
        missing_wells = [w for w in (pos, neg) if check_input  # False: input not readable yet
                         and not well_present_in_input(input_dir, w, schema=schema, sep=sep)]
        if missing_wells:
            label = ", ".join(f"'{w}'" for w in missing_wells)
            return (f"Control well {label} was not found in the input folder.\n\n"
                    f"The threshold pass reads each control well's measurements "
                    f"after the run — confirm they exist in:\n{input_dir}")
        return None

    def _collect_run_options(self) -> dict:
        """The form's fields as one run-options dict. Every numeric field is
        carried as the **raw text** the user typed: coercing here would put the
        value out of ``_numeric_option_errors``' reach, which cannot tell a typo
        from a deliberate blank once both have become ``0.0``.
        ``build_pipeline_args`` coerces once, after validation has had its say.
        """
        method = self._seg_method()
        cyto = self._cytoplasm_edit.text().strip()
        if method != "stardist_seeded_watershed_cell":
            cyto = ""
        sd_prob, sd_nms = self._stardist_threshold_values()
        opts = dict(
            raw=Path(raw_text) if (raw_text := self._input_edit.text().strip()) else "",
            scratch_dir=self._scratch_edit.text().strip() or None,
            nuclear_token=self._nuclear_token_edit.text().strip() or "NIR",
            fluor_tokens=self._fluor_tokens_list(),
            csv_prefix=self._csv_prefix_edit.text().strip() or "measurements",
            tophat_radius_nir=self._tophat_radius_nir_edit.text(),
            tophat_radius_fluor=self._tophat_radius_fluor_edit.text(),
            fft_small_nir=self._fft_small_nir_edit.text(),
            fft_small_fluor=self._fft_small_fluor_edit.text(),
            mesh_ratio=self._mesh_ratio_edit.text(),
            mesh_sigma=self._mesh_sigma_edit.text(),
            no_tophat_nir=self._no_tophat_nir_cb.isChecked(),
            no_tophat_fluor=self._no_tophat_fluor_cb.isChecked(),
            # Always-on (no longer user options): zip in/out + move loose TIFs.
            compress_input_well_folders=True,
            compress_output_well_folders=True,
            move_source_tifs=True,
            remote_batch_size=self._remote_batch_size_edit.text(),
            force=self._force_cb.isChecked(),
            # services.input_resolution_service.select_incremental_scope may
            # still override this during input resolution (it forces a full
            # reprocess when loose images land under a cross-well flat-field /
            # background model), but it never clears a True the form set: with
            # nothing appended it returns before touching the key.
            incremental=self._incremental_cb.isChecked(),
            **well_scope_options(self),
            **storage_options(self),
            cpu_only=self._cpu_only_cb.isChecked(),
            allow_low_disk=self._allow_low_disk_cb.isChecked(),
            tf_threads=self._tf_threads_edit.text(),
            workers=self._workers_edit.text(),
            stardist_tile_px=self._stardist_tile_px_edit.text(),
            stardist_prob_thresh=sd_prob,
            stardist_nms_thresh=sd_nms,
            filename_schema=self._build_schema_arg(),
            filename_sep=self._schema_picker.separator(),
            smfish_tokens=self._smfish_tokens_list(),
            shape_features=list(self._shape_chips.checkedData()),
            flatfield=self._flatfield_cb.currentText(),
            background=self._background_cb.currentText(),
            background_well=self._background_well_edit.text().strip(),
            background_well_map=self._background_map_value(),
            auto_threshold=self._auto_threshold_cb.isChecked(),
            run_check=self._run_check_cb.isChecked(),
            auto_threshold_method=self._auto_threshold_method(),
            pos_control_well=self._pos_control_well_edit.text().strip(),
            neg_control_well=self._neg_control_well_edit.text().strip(),
            qc_remove_debris=self._qc_remove_debris_cb.isChecked(),
            qc_min_area=self._qc_min_area_edit.text().strip(),
            qc_max_area=self._qc_max_area_edit.text().strip(),
            qc_min_solidity=self._qc_min_solidity_edit.text().strip(),
            measure_focus=self._focus_metric_cb.isChecked(),
            focus_min=self._focus_min_edit.text().strip(),
            phase_token=self._phase_token(),
            segmentation_method=method,
            cytoplasm_token=cyto,
            min_nucleus_area_px=self._min_nucleus_area_edit.text().strip(),
            cellpose_model=self._cellpose_model_cb.currentText(),
            cellpose_diameter=self._cellpose_diameter_edit.text().strip(),
            cellpose_flow_threshold=self._cellpose_flow_thr_edit.text().strip(),
            cellpose_cellprob_threshold=self._cellpose_cellprob_thr_edit.text().strip(),
            cellpose_nuclei_channel=self._cellpose_nuclei_channel_edit.text().strip(),
            **self._nanowell_option_values(),
        )
        return opts
