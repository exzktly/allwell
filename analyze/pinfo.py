"""Restoring a prior run's settings into the Analyze form.

``AnalyzeTabFormMixin`` inherits :class:`AnalyzePipelineInfoMixin` for the one
cohesive job of reading a ``pipeline_info.json`` sidecar back into every form
field — the "Load previous run" action, the OME-TIFF converter's channel-role
handoff, and Validate & Repair's "Complete this run in Analyze" all arrive
here. Split out of ``analyze/form.py`` for its line budget; the methods
bind to ``self`` exactly as before, so this is a pure relocation.

The sidecar is the only durable record of how a dataset was produced, which is
why restoring it faithfully matters beyond convenience: a re-run whose
``csv_prefix`` or schema doesn't match the original reads its already-done set
from files that don't exist, and silently reprocesses (or skips) the wrong
work.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from well_viewer.core.safe import swallowed


class AnalyzePipelineInfoMixin:
    """Read a ``pipeline_info.json`` sidecar back into the Analyze form."""

    # ── Load a prior run's settings from its pipeline_info.json sidecar ───────
    def _open_pipeline_info(self) -> None:
        """Pick a pipeline_info.json and repopulate every form field from it."""
        start = ""
        try:
            start = self._input_edit.text().strip()
        except Exception:
            swallowed()
        path, _ = QFileDialog.getOpenFileName(
            self, "Open pipeline_info.json", start,
            "Pipeline info (pipeline_info.json);;JSON (*.json);;All files (*.*)",
        )
        if not path:
            return
        try:
            info = json.loads(Path(path).read_text())
            if not isinstance(info, dict):
                raise ValueError("not a pipeline_info object")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Open failed",
                                 f"Could not read pipeline_info:\n{exc}")
            return
        try:
            self._apply_pipeline_info(info, source=Path(path))
        except Exception as exc:  # never crash the slot
            QMessageBox.warning(self, "Partial load",
                                f"Some fields could not be restored:\n{exc}")

    @staticmethod
    def _pinfo_flat(info: dict) -> dict:
        """Flatten a pipeline_info dict to argparse-style keys.

        Prefers ``execution_options`` (the full ``vars(args)`` snapshot recorded
        by the pipeline, v2+); falls back to the top-level / ``qc_options`` keys
        for older sidecars that predate that block.
        """
        exec_opts = info.get("execution_options")
        if isinstance(exec_opts, dict) and exec_opts:
            return dict(exec_opts)
        qc = info.get("qc_options") or {}
        flat = {
            "filename_schema": info.get("schema"),
            "filename_sep": info.get("separator"),
            "nuclear_token": info.get("nuclear_token"),
            "fluor_tokens": info.get("fluor_tokens"),
            "smfish_tokens": info.get("smfish_tokens"),
            "shape_features": info.get("shape_features"),
            "segmentation_method": info.get("segmentation_method"),
            "cytoplasm_token": info.get("cytoplasm_token"),
            "min_nucleus_area_px": info.get("min_nucleus_area_px"),
            "phase_token": info.get("phase_token"),
            "flatfield": qc.get("flatfield"),
            "background": qc.get("background"),
            "background_well": qc.get("background_well"),
            "fft_small_nir": qc.get("fft_small_nir"),
            "fft_small_fluor": qc.get("fft_small_fluor"),
            "mesh_ratio": qc.get("mesh_ratio"),
            "mesh_sigma": qc.get("mesh_sigma"),
            "qc_remove_debris": qc.get("remove_debris"),
            "qc_min_area": qc.get("min_area"),
            "qc_max_area": qc.get("max_area"),
            "qc_min_solidity": qc.get("min_solidity"),
        }
        return {k: v for k, v in flat.items() if v is not None}

    def _restore_channel_rows(self, d: dict) -> None:
        """Apply the channel-token keys of a flattened sidecar to the form's rows.

        An absent key means "leave this alone", the same rule the scalar setters
        follow. That has to be spelled out here because the list is not restored
        field by field: an empty ``fluor_tokens`` reaching ``_set_fluor_rows`` is
        read as "no channels" and collapses the rows to the single default one,
        so a caller that knows nothing about channels — the folder scan that
        recognised no channel slot, say — would wipe a hand-typed list. A caller
        naming only the flags applies them to the rows already on the form.
        """
        if not any(k in d for k in ("fluor_tokens", "smfish_tokens", "phase_token")):
            return
        tokens = d.get("fluor_tokens")
        self._set_fluor_rows(
            list(tokens) if tokens else self._fluor_tokens_list(),
            smfish=list(d.get("smfish_tokens") or []),
            phase=str(d.get("phase_token") or "").strip(),
        )

    def _apply_input_path(self, d: dict, source: Path | None) -> None:
        """Point the form's input field at the dataset a sidecar describes.

        Split out of :meth:`_apply_pipeline_info` because it is the one part of
        applying a sidecar that only one of the two callers wants, and because
        the branch it needed pushed that method past the complexity ratchet.

        Prefers the recorded ``input_dir``; a sidecar that carries none is
        located from its own folder, since it lives in the output directory and
        the input is that directory's sibling.
        """
        in_dir = d.get("input_dir")
        if not in_dir and source is not None:
            out = source.parent
            sib = out.parent / "in"
            in_dir = str(sib if sib.is_dir() else out.parent)
        if not in_dir:
            return
        self._input_edit.setEnabled(True)
        if getattr(self, "_browse_btn", None) is not None:
            self._browse_btn.setEnabled(True)
        self._input_edit.setText(str(in_dir))

    def _apply_pipeline_info(self, info: dict, *, source: Path | None = None,
                             set_input_path: bool = True) -> None:
        """Apply a sidecar's settings to the form.

        ``set_input_path`` exists because the two callers want opposite things
        from the same payload. **Open pipeline_info.json** is the user saying
        "point me at the dataset this file describes", so the path is the point.
        The pre-run readout's *Load these settings* is the user saying "keep the
        folder I chose, take the settings from what is already in it" — and
        ``execution_options`` is a ``vars(args)`` snapshot, so it always carries
        the ``input_dir`` of the machine the run happened on. Applying it there
        replaced the user's own folder with a path that need not exist locally
        at all, on every dataset produced by anyone else.
        """
        d = self._pinfo_flat(info)

        def s(attr, val):
            w = getattr(self, attr, None)
            if w is not None and val is not None:
                w.setText(str(val))

        def chk(attr, val):
            w = getattr(self, attr, None)
            if w is not None and val is not None:
                w.setChecked(bool(val))

        def combo(attr, val):
            w = getattr(self, attr, None)
            if w is not None and val:
                w.setCurrentText(str(val))

        # Schema first (separator, then the string → the picker syncs its own
        # per-field combos from it).
        picker = getattr(self, "_schema_picker", None)
        if picker is not None:
            if d.get("filename_sep"):
                picker.setSeparator(str(d["filename_sep"]))
            if d.get("filename_schema"):
                picker.setSchema(str(d["filename_schema"]))

        # Segmentation method — the combo carries the canonical value as itemData.
        method = d.get("segmentation_method")
        if method:
            idx = self._seg_method_cb.findData(method)
            if idx >= 0:
                self._seg_method_cb.setCurrentIndex(idx)

        # Channel tokens.
        s("_nuclear_token_edit", d.get("nuclear_token"))
        self._restore_channel_rows(d)

        # Cellpose / watershed params.
        combo("_cellpose_model_cb", d.get("cellpose_model"))
        s("_cellpose_diameter_edit", d.get("cellpose_diameter"))
        s("_cellpose_flow_thr_edit", d.get("cellpose_flow_threshold"))
        s("_cellpose_cellprob_thr_edit", d.get("cellpose_cellprob_threshold"))
        s("_cellpose_nuclei_channel_edit", d.get("cellpose_nuclei_channel"))
        s("_cytoplasm_edit", d.get("cytoplasm_token"))
        s("_min_nucleus_area_edit", d.get("min_nucleus_area_px"))
        self._restore_stardist_thresholds(d)

        # Shape features.
        feats = d.get("shape_features")
        if feats is not None and hasattr(self, "_shape_chips"):
            try:
                self._shape_chips.setCheckedData(list(feats))
            except Exception:
                swallowed()

        # QC.
        combo("_flatfield_cb", d.get("flatfield"))
        combo("_background_cb", d.get("background"))
        s("_background_well_edit", d.get("background_well"))
        # The sidecar records the NEGATIVE (`--no_auto_threshold`), so the
        # checkbox is its inverse; setChecked fires toggled and syncs the rows.
        nat = d.get("no_auto_threshold")
        if nat is not None and hasattr(self, "_auto_threshold_cb"):
            self._auto_threshold_cb.setChecked(not bool(nat))
        # Same inversion for the completeness check. Absent means the run
        # predates the flag, and the checkbox keeps its own default rather than
        # inheriting a decision nobody made.
        nrc = d.get("no_run_check")
        if nrc is not None and hasattr(self, "_run_check_cb"):
            self._run_check_cb.setChecked(not bool(nrc))
        s("_pos_control_well_edit", d.get("pos_control_well"))
        s("_neg_control_well_edit", d.get("neg_control_well"))
        # The sidecar records wells, not a method — naming both IS the method on
        # the pipeline side, so infer the combo back from them.
        if hasattr(self, "_auto_threshold_method_cb"):
            used_controls = bool(str(d.get("pos_control_well") or "").strip()
                                 and str(d.get("neg_control_well") or "").strip())
            idx = self._auto_threshold_method_cb.findData(
                "controls" if used_controls else "otsu")
            if idx >= 0:
                self._auto_threshold_method_cb.setCurrentIndex(idx)
        chk("_qc_remove_debris_cb", d.get("qc_remove_debris"))
        s("_qc_min_area_edit", d.get("qc_min_area"))
        s("_qc_max_area_edit", d.get("qc_max_area"))
        s("_qc_min_solidity_edit", d.get("qc_min_solidity"))
        s("_tophat_radius_nir_edit", d.get("tophat_radius_nir"))
        s("_tophat_radius_fluor_edit", d.get("tophat_radius_fluor"))
        chk("_no_tophat_nir_cb", d.get("no_tophat_nir"))
        chk("_no_tophat_fluor_cb", d.get("no_tophat_fluor"))
        s("_fft_small_nir_edit", d.get("fft_small_nir"))
        s("_fft_small_fluor_edit", d.get("fft_small_fluor"))
        s("_mesh_ratio_edit", d.get("mesh_ratio"))
        s("_mesh_sigma_edit", d.get("mesh_sigma"))

        # Output / compute.
        s("_csv_prefix_edit", d.get("csv_prefix"))
        s("_tf_threads_edit", d.get("tf_threads"))
        s("_workers_edit", d.get("workers"))
        s("_stardist_tile_px_edit", d.get("stardist_tile_px"))
        chk("_cpu_only_cb", d.get("cpu_only"))
        # ``allow_low_disk`` is deliberately NOT restored, though the sidecar
        # records it. Every other option here describes how a plate was
        # analysed and is worth reproducing; that one disarms a check that
        # exists because a volume filling part-way costs hours of work, and
        # re-arming it from a folder someone happened to open would be a safety
        # override the user never chose on a disk they may not have measured.
        # It is a per-run decision, so it starts unticked every time.
        chk("_force_cb", d.get("force"))
        chk("_incremental_cb", d.get("incremental"))
        forced = d.get("force_wells")
        if forced is not None and hasattr(self, "_force_wells_edit"):
            self._force_wells_edit.setText(", ".join(str(w) for w in forced))

        # Scratch disk — a recorded value pins the field; absent, it stays blank
        # so the pipeline default (~/.allwell/scratch) applies.
        scratch = d.get("scratch_dir")
        if scratch and hasattr(self, "_scratch_edit"):
            self._scratch_edit.setText(str(scratch))
            self._scratch_user_set = True

        # Paths last, and only when the caller wants them — see the docstring.
        if set_input_path:
            self._apply_input_path(d, source)
        for refresh in ("_refresh_output", "_refresh_schema_preview",
                        "_refresh_segmentation_hints"):
            fn = getattr(self, refresh, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    swallowed()
        try:
            self._dashboard.set_status(
                f"Loaded settings from {source.name}" if source else "Loaded settings")
        except Exception:
            swallowed()
