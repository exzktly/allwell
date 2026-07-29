"""Packed OME-TIFF input handling for the Analyze pane, split from the monoliths.

``AnalyzeTab`` mixes in :class:`AnalyzeOmeMixin` alongside
:class:`analyze_tab_form.AnalyzeTabFormMixin`. This module owns the whole
single-file-OME-TIFF path so neither ``analyze_tab.py`` nor ``analyze_tab_form.py``
carries it:

* the collapsible **Single OME-TIFF Input** form section (well-grid dimensions,
  origin, serpentine direction) and its live position/well readout,
* the file picker and the folder-status labels shown when a packed file is
  selected,
* channel-token prefill from the OME channel names,
* pre-run validation of the grid mapping, and
* :meth:`_resolve_run_dirs_for_runner` — the adapter handed to
  :class:`services.pipeline_runner.PipelineRunner`, which now runs the
  de-interleave pre-pass (:func:`services.input_resolution_service.explode_ome_if_present`)
  before the normal loose-TIF grouping.

The methods bind to ``self`` exactly as before (they read the form widgets and
state the tab owns), so this is a behaviour-preserving relocation.
"""

from __future__ import annotations

import queue
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QFileDialog, QLabel, QPushButton

from services.input_resolution_service import (
    explode_ome_if_present,
    resolve_input_output,
    select_incremental_scope,
)
from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP
from well_viewer.core.safe import swallowed


class AnalyzeOmeMixin:
    """Single packed OME-TIFF input: form controls, prefill, and run-dir resolution."""

    # ------------------------------------------------------------------
    # Form section
    # ------------------------------------------------------------------
    def _build_ome_section(self, parent) -> None:
        """Grid/origin controls shown only when a packed OME-TIFF is selected.

        A single OME-TIFF holds every timepoint, channel, Z-plane and stage
        position of a plate scan. The positions are acquired in serpentine order,
        so their well labels are reconstructed from the imaged well-grid
        dimensions plus the origin the user supplies here. Each position is one
        well × field-of-view; FOVs-per-well is derived from the position count.
        """
        sec = self._section(
            parent, "Single OME-TIFF Input",
            info="Shown when the input is one packed .ome.tif. Enter the "
                 "dimensions of the imaged well block so each serpentine "
                 "stage position can be auto-labelled with its well.",
        )
        # The container (``_section`` returns the body) is toggled as a whole.
        self._ome_section = sec.parentWidget()

        grid_row = self._row(sec, "Well grid (rows × cols)")
        self._ome_rows_edit = self._entry(grid_row, "1", width=48)
        grid_row.layout().addWidget(QLabel("×", grid_row))
        self._ome_cols_edit = self._entry(grid_row, "1", width=48)
        grid_row.layout().addStretch(1)

        origin_row = self._row(sec, "Origin well (top-left)")
        self._ome_origin_edit = self._entry(origin_row, "A01", width=60)
        self._ome_origin_edit.setPlaceholderText("A01")
        origin_row.layout().addStretch(1)

        dir_row = self._row(sec, "Serpentine direction")
        self._ome_direction_cb = QComboBox(dir_row)
        self._ome_direction_cb.addItem("Snake by row", userData="row")
        self._ome_direction_cb.addItem("Snake by column", userData="col")
        self._ome_direction_cb.setFixedWidth(160)
        dir_row.layout().addWidget(self._ome_direction_cb)
        dir_row.layout().addStretch(1)

        self._ome_info_lbl = QLabel("", sec)
        self._ome_info_lbl.setObjectName("Muted")
        self._ome_info_lbl.setWordWrap(True)
        sec.layout().addWidget(self._ome_info_lbl)

        for w in (self._ome_rows_edit, self._ome_cols_edit, self._ome_origin_edit):
            w.textChanged.connect(lambda _=None: self._refresh_ome_readout())
        self._ome_direction_cb.currentIndexChanged.connect(
            lambda _=None: self._refresh_ome_readout())
        self._ome_section.setVisible(False)

    def _attach_ome_browse_button(self, container, layout) -> None:
        """Add the 'OME file…' picker to the Folders row's *layout*.

        The folder picker there cannot select a file, so a packed .ome.tif needs
        its own action. Enabled unconditionally — it synthesizes its own filename
        schema on de-interleave, so it doesn't depend on the schema fields.
        """
        self._browse_ome_btn = QPushButton("OME file…", container)
        self._browse_ome_btn.setProperty("variant", "secondary")
        self._browse_ome_btn.setToolTip(
            "Select a single packed OME-TIFF holding every well, channel and "
            "timepoint. It is de-interleaved into per-well images before analysis."
        )
        self._browse_ome_btn.clicked.connect(self._browse_ome_input)
        layout.addWidget(self._browse_ome_btn)

    def _browse_ome_input(self) -> None:
        """Pick a single packed OME-TIFF file directly (not a folder)."""
        f, _ = QFileDialog.getOpenFileName(
            self, "Select OME-TIFF file", "",
            "OME-TIFF (*.ome.tif *.ome.tiff);;All files (*)",
        )
        if f:
            self._input_edit.setText(f)
            self._input_edit.setEnabled(True)

    # ------------------------------------------------------------------
    # Detection + live readout
    # ------------------------------------------------------------------
    def _apply_ome_output_labels(self) -> bool:
        """Detect a packed OME-TIFF and, if found, set the Folders labels.

        Returns True when the input is a packed OME-TIFF (so ``_refresh_output``
        skips its loose-TIF / well-folder detection), False otherwise.
        """
        self._refresh_ome_section()
        if self._ome_detected_file is None:
            return False
        base = self._ome_detected_file.parent
        self._output_lbl.setText(str(base / "out"))
        self._layout_lbl.setText(
            "✓ Packed OME-TIFF — will de-interleave into per-well images")
        return True

    def _ome_grid_dims(self) -> tuple[int, int, bool]:
        """Parsed ``(rows, cols, ok)`` from the grid fields; ok=False if unusable."""
        try:
            rows = int(self._ome_rows_edit.text().strip())
            cols = int(self._ome_cols_edit.text().strip())
        except (AttributeError, ValueError):
            return 0, 0, False
        return rows, cols, rows > 0 and cols > 0

    def _ome_direction(self) -> str:
        """``"row"`` or ``"col"`` — the selected serpentine snake axis."""
        return str(self._ome_direction_cb.currentData() or "row")

    def _ome_read_layout(self, path):
        """Read (and cache) the OME layout metadata for *path*, or ``None``.

        Cached by ``(path, mtime)`` so the readout can refresh on every keystroke
        without re-opening the file. Any read failure returns ``None`` rather than
        raising into the Qt event loop.
        """
        if path is None:
            return None
        cache = getattr(self, "_ome_layout_cache", None)
        if cache is None:
            cache = self._ome_layout_cache = {}
        try:
            key = (str(path), Path(path).stat().st_mtime)
        except OSError:
            return None
        if key in cache:
            return cache[key]
        try:
            from pipeline.ome_explode import read_ome_layout
            layout = read_ome_layout(path)
        except Exception:
            swallowed()
            layout = None
        cache[key] = layout
        return layout

    def _ome_prefill_channels(self, path) -> None:
        """Auto-fill the nuclear + fluor channel tokens from OME channel names."""
        layout = self._ome_read_layout(path)
        names = list(getattr(layout, "channel_names", []) or [])
        if not names or not hasattr(self, "_nuclear_token_edit"):
            return
        self._nuclear_token_edit.setText(names[0])
        fluors = names[1:] or names[:1]
        self._set_fluor_rows(fluors, smfish=[], phase="")

    def _set_fluor_rows(self, tokens, *, smfish, phase) -> None:
        """Repopulate the fluor-channel rows to *tokens*, restoring smFISH/Phase flags.

        Shared by the OME channel prefill above and by the 'load previous run'
        restore in :class:`analyze_tab_form.AnalyzeTabFormMixin`; both reach it
        via ``self``.
        """
        tokens = [str(t).strip() for t in tokens if str(t).strip()] or ["GFP"]
        # Collapse to a single row, then grow to match the loaded tokens.
        while len(self._fluor_rows) > 1:
            _e, _s, _p, b = self._fluor_rows[-1]
            self._fluor_remove_row(b.parent(), b)
        self._fluor_rows[0][0].setText(tokens[0])
        for tok in tokens[1:]:
            self._fluor_add_row(tok)
        sm, ph = set(smfish or []), (phase or "").strip()
        for e, s_cb, p_cb, _b in self._fluor_rows:
            tok = e.text().strip()
            s_cb.setChecked(tok in sm)
            p_cb.setChecked(bool(ph) and tok == ph)

    def _refresh_ome_section(self) -> None:
        """Detect a packed OME-TIFF under the input and toggle the section."""
        detected = None
        raw_str = self._input_edit.text().strip() if hasattr(self, "_input_edit") else ""
        if raw_str:
            try:
                from services.input_resolution_service import detect_ome_input
                detected = detect_ome_input(Path(raw_str))
            except Exception:
                swallowed()
        self._ome_detected_file = detected
        if not hasattr(self, "_ome_section"):
            return
        self._ome_section.setVisible(detected is not None)
        if detected is not None:
            if getattr(self, "_ome_prefilled_for", None) != str(detected):
                self._ome_prefill_channels(detected)
                self._ome_prefilled_for = str(detected)
            self._refresh_ome_readout()

    def _refresh_ome_readout(self) -> None:
        """Update the position/well summary shown under the grid controls."""
        if not hasattr(self, "_ome_info_lbl") or self._ome_detected_file is None:
            return
        layout = self._ome_read_layout(self._ome_detected_file)
        if layout is None:
            self._ome_info_lbl.setText(
                "⚠ Could not read OME metadata from the selected file.")
            return
        info = (f"{layout.n_positions} position(s) · {layout.size_c} channel(s) · "
                f"{layout.size_t} timepoint(s)")
        if layout.size_z > 1:
            info += f" · {layout.size_z} Z (max-projected)"
        rows, cols, ok = self._ome_grid_dims()
        if ok:
            n_wells = rows * cols
            if layout.n_positions % n_wells != 0:
                info += (f"\n⚠ {layout.n_positions} positions not divisible by "
                         f"{rows}×{cols} = {n_wells} wells.")
            else:
                fpw = layout.n_positions // n_wells
                try:
                    from util.plate_grid import serpentine_well_labels
                    labels = serpentine_well_labels(
                        rows, cols,
                        origin=self._ome_origin_edit.text().strip() or "A01",
                        direction=self._ome_direction(),
                    )
                    preview = ", ".join(labels[:6]) + (" …" if len(labels) > 6 else "")
                    info += f"\n→ {n_wells} well(s) × {fpw} FOV(s). Wells: {preview}"
                except ValueError as exc:
                    info += f"\n⚠ {exc}"
        self._ome_info_lbl.setText(info)

    # ------------------------------------------------------------------
    # Validation + run-option merge
    # ------------------------------------------------------------------
    def _ome_validation_error(self) -> str | None:
        """Block the run when the OME grid can't map to valid wells; else ``None``.

        No-op (returns ``None``) when the input isn't a packed OME-TIFF.
        """
        if getattr(self, "_ome_detected_file", None) is None:
            return None
        rows, cols, ok = self._ome_grid_dims()
        if not ok:
            return ("Enter the imaged well-grid dimensions (rows × columns) as "
                    "positive whole numbers so each stage position can be "
                    "labelled with its well.")
        layout = self._ome_read_layout(self._ome_detected_file)
        if layout is None:
            return ("The selected .ome.tif could not be read. Confirm it is a "
                    "valid OME-TIFF.")
        n_wells = rows * cols
        if layout.n_positions % n_wells != 0:
            return (f"The file has {layout.n_positions} stage position(s), which "
                    f"is not divisible by the {rows}×{cols} = {n_wells} well "
                    f"grid.\n\nFields-of-view per well must be a whole number — "
                    f"check the grid dimensions.")
        try:
            from util.plate_grid import serpentine_well_labels
            serpentine_well_labels(
                rows, cols,
                origin=self._ome_origin_edit.text().strip() or "A01",
                direction=self._ome_direction(),
            )
        except ValueError as exc:
            return str(exc)
        return None

    def _merge_ome_run_options(self, opts: dict) -> None:
        """Fold the OME de-interleave parameters into the run *opts* dict.

        Ignored downstream unless ``ome_input`` is set. When a packed OME-TIFF is
        the input, the schema/separator are forced to the defaults the explode
        step writes, regardless of the (unused) schema picker.
        """
        ome_on = getattr(self, "_ome_detected_file", None) is not None
        rows, cols, _ = self._ome_grid_dims() if ome_on else (0, 0, False)
        opts["ome_input"] = ome_on
        opts["ome_grid_rows"] = rows
        opts["ome_grid_cols"] = cols
        opts["ome_origin_well"] = (
            (self._ome_origin_edit.text().strip() or "A01") if ome_on else "A01")
        opts["ome_direction"] = self._ome_direction() if ome_on else "row"
        if ome_on:
            opts["filename_schema"] = DEFAULT_SCHEMA
            opts["filename_sep"] = DEFAULT_SEP

    # ------------------------------------------------------------------
    # Run-directory resolution (includes the OME de-interleave pre-pass)
    # ------------------------------------------------------------------
    def _resolve_run_dirs_for_runner(self, opts: dict, log_q: queue.Queue,
                                     *, proc_hook=None):
        """Adapter passed to ``PipelineRunner.start``.

        Lives here because it hosts the OME de-interleave pre-pass. A packed
        OME-TIFF is first exploded into loose per-plane TIFs in a sibling ``in/``
        folder; the returned base dir then flows through the normal loose-TIF
        grouping path unchanged. Expected-well-count + grouping progress are
        UI-side helpers that depend on Analyze form state. ``proc_hook`` is
        forwarded to ``resolve_input_output`` so the grouping subprocess can be
        tracked by the runner and reached by Stop.
        """
        try:
            log_q.put(("zipper_start", self._expected_well_count(opts)))

            emitted: set[str] = set()

            def _on_group(tok: str) -> None:
                emitted.add(tok)
                log_q.put(("zipper_well", tok))

            raw = opts["raw"]
            exploded_base = explode_ome_if_present(
                raw, opts, log_fn=lambda m: log_q.put(("line", m)))
            if exploded_base is not None:
                raw = exploded_base

            input_dir, output_dir, on_success = resolve_input_output(
                raw,
                log_fn=lambda msg: log_q.put(("line", msg)),
                progress_fn=_on_group,
                filename_schema=opts["filename_schema"],
                filename_sep=opts["filename_sep"],
                proc_hook=proc_hook,
                move_source_tifs=bool(opts.get("move_source_tifs", False)),
            )
            # Fold loose images into well zips, then pick run scope (mutates opts).
            select_incremental_scope(opts, input_dir,
                                     log_fn=lambda m: log_q.put(("line", m)))
            # Surface every resolved well in the Group stage (pre-grouped wells
            # emit no zipper progress) so none are missing from the plate/rail.
            from services.input_resolution_service import iter_well_tokens
            for tok in iter_well_tokens(input_dir):
                if tok not in emitted:
                    log_q.put(("zipper_well", tok))
            log_q.put(("zipper_done", None))
            # Forward the optional success callback as a 3-tuple. PipelineRunner
            # unpacks it and runs it after a clean pipeline completion so loose
            # TIFs at raw/ get cleaned up once raw/in/ has the zipped equivalents.
            return input_dir, output_dir, on_success
        except (ValueError, RuntimeError) as exc:
            log_q.put(("error", f"Input error: {exc}\n"))
            return None
