"""Analyze-pane input handling, split from ``analyze_tab.py`` for line budget.

``AnalyzeTab`` mixes in :class:`AnalyzeInputMixin` for three members that don't
belong in the tab's run/log core:

* ``_resolve_run_dirs_for_runner`` — the adapter handed to
  :class:`services.pipeline_runner.PipelineRunner`, which resolves the selected
  folder into ``(input_dir, output_dir, on_success)`` and streams grouping
  progress. Analyze only ever processes **folders**: a packed OME-TIFF is
  converted to a dataset folder first, in the Datasets → *Convert OME-TIFF* page,
  so no de-interleave happens here.
* ``_ome_input_hint`` — a guard that spots a raw ``.ome.tif`` selected as input
  and points the user at that converter instead of trying to process it.
* ``_set_fluor_rows`` — repopulates the fluorescent-channel rows, shared with the
  form's "load previous run" restore.

They bind to ``self`` exactly as before — a behaviour-preserving relocation.
"""

from __future__ import annotations

import queue
from pathlib import Path

from services.input_resolution_service import (
    detect_ome_input,
    resolve_input_output,
    select_incremental_scope,
)

_CONVERT_HINT = (
    "This is a packed OME-TIFF. Convert it to a dataset folder first in the "
    "Datasets tab → “Convert OME-TIFF”, then open that folder here to process it."
)


class AnalyzeInputMixin:
    """Input-folder resolution + the OME-TIFF redirect for the Analyze pane."""

    def _ome_input_hint(self) -> str | None:
        """Return a redirect message when the input is a raw OME-TIFF, else ``None``.

        Analyze processes folders; a packed OME-TIFF must be de-interleaved in the
        Datasets converter first.
        """
        raw_str = self._input_edit.text().strip() if hasattr(self, "_input_edit") else ""
        if not raw_str:
            return None
        try:
            detected = detect_ome_input(Path(raw_str))
        except Exception:  # pragma: no cover — defensive
            return None
        return _CONVERT_HINT if detected is not None else None

    def _resolve_run_dirs_for_runner(self, opts: dict, log_q: queue.Queue,
                                     *, proc_hook=None):
        """Adapter passed to ``PipelineRunner.start``.

        Resolves ``opts['raw']`` into ``(input_dir, output_dir, on_success)`` via
        :func:`resolve_input_output`, folding loose images into well zips and
        picking the run scope. Expected-well-count + grouping progress are
        UI-side helpers that depend on Analyze form state; ``proc_hook`` is
        forwarded so the grouping subprocess can be tracked and reached by Stop.
        """
        try:
            log_q.put(("zipper_start", self._expected_well_count(opts)))

            emitted: set[str] = set()

            def _on_group(tok: str) -> None:
                emitted.add(tok)
                log_q.put(("zipper_well", tok))

            input_dir, output_dir, on_success = resolve_input_output(
                opts["raw"],
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

    def _set_fluor_rows(self, tokens, *, smfish, phase) -> None:
        """Repopulate the fluor-channel rows to *tokens*, restoring smFISH/Phase flags.

        Shared with the form's "load previous run" restore; both reach it via
        ``self``.
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
