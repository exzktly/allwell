"""Analyze-pane input handling, split from ``analyze/tab.py`` for line budget.

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
* ``_autofill_from_input_scan`` / ``_report_scan_guess`` — the browse-time guess
  at the folder's filename schema and channel roles, and the status line that
  says what it guessed.

They bind to ``self`` exactly as before — a behaviour-preserving relocation.
"""

from __future__ import annotations

import queue
from pathlib import Path

from services.input_resolution_service import (
    PACKED_OME_MESSAGE,
    detect_ome_input,
    resolve_input_output,
    select_incremental_scope,
)
from util.schema_infer import infer_from_dir
from well_viewer.core.safe import swallowed


class AnalyzeInputMixin:
    """Input-folder resolution + the OME-TIFF redirect for the Analyze pane."""

    def _ome_input_hint(self) -> str | None:
        """Return a message that must block the run, or ``None`` to let it start.

        Analyze processes folders; a packed OME-TIFF must be de-interleaved in
        the Datasets converter first, so one selected here earns
        :data:`PACKED_OME_MESSAGE`.

        A check that could not be *made* is reported too, rather than read as a
        pass. The guard used to answer ``None`` for any exception — a permission
        error part-way through the walk, an unreadable directory — which is the
        one answer that means "carry on", so the run went ahead against an input
        Analyze cannot process and the redirect this guard exists for never
        fired. Saying so costs a refusal the user can act on; failing open costs
        a run.
        """
        raw_str = self._input_edit.text().strip() if hasattr(self, "_input_edit") else ""
        if not raw_str:
            return None
        try:
            detected = detect_ome_input(Path(raw_str))
        except Exception as exc:
            # Deliberately broad, and deliberately not silent: this runs from
            # the Run button's own slot, so a type not listed here would kill
            # the click rather than report anything (see the Manage-Remotes
            # dead-button defect this audit found next door).
            return (f"Could not inspect the selected input:\n{raw_str}\n\n"
                    f"{type(exc).__name__}: {exc}\n\n"
                    "Analyze checks here whether the folder holds a packed "
                    "OME-TIFF, which has to be converted in Datasets first. "
                    "The check did not complete, so the run is not started.")
        return PACKED_OME_MESSAGE if detected is not None else None

    def _resolve_run_dirs_for_runner(self, opts: dict, log_q: queue.Queue,
                                     *, proc_hook=None) -> tuple | None:
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

        The Phase designation lives on a channel row, so a *phase* token naming a
        channel that is not among *tokens* would have nowhere to go. Rather than
        drop it — which silently disables the phase passthrough and loses the
        channel — the token is appended as its own row. Matching is
        case-insensitive because the sidecar records ``phase_token`` lower-cased
        while the channel list keeps whatever case the filenames use.
        """
        tokens = [str(t).strip() for t in tokens if str(t).strip()] or ["GFP"]
        ph = (phase or "").strip()
        if ph and ph.lower() not in {t.lower() for t in tokens}:
            tokens = [*tokens, ph]
        # Collapse to a single row, then grow to match the loaded tokens.
        while len(self._fluor_rows) > 1:
            _e, _s, _p, b = self._fluor_rows[-1]
            self._fluor_remove_row(b.parent(), b)
        self._fluor_rows[0][0].setText(tokens[0])
        for tok in tokens[1:]:
            self._fluor_add_row(tok)
        sm = {str(t).strip().lower() for t in (smfish or [])}
        for e, s_cb, p_cb, _b in self._fluor_rows:
            tok = e.text().strip().lower()
            s_cb.setChecked(tok in sm)
            p_cb.setChecked(bool(ph) and tok == ph.lower())

    def _autofill_from_input_scan(self, folder: str) -> None:
        """Pre-fill the schema + channel fields by briefly scanning *folder*.

        Samples the filenames on disk (loose TIFs, a zip, or the per-well-zip
        layout) and guesses the separator, slot layout, channel tokens and their
        roles, applying them like a loaded ``pipeline_info``. Best-effort: a
        folder whose names don't parse (or an unreadable one) leaves the form as
        the user set it. Only the interactive picker calls this — the converter's
        "Open in Analyze" handoff supplies its own channel roles.

        A scan that identified no channel slot contributes no channel keys at
        all. An empty ``fluor_tokens`` list would otherwise reach
        ``_set_fluor_rows``, which reads it as "no channels" and resets the rows
        to the single default one — wiping a list the user typed by hand. The
        channels it *did* find, and which of them it handed the segmentation
        role, are named in the dashboard status line: the guess overwrites two
        fields whose new values look no different from hand-entered ones, so
        without saying so it is invisible until it turns up in a sidecar after a
        run.
        """
        try:
            guess = infer_from_dir(folder)
        except Exception:
            swallowed()  # a scan must never break folder selection
            return
        if guess is None:
            return
        # Sidecar-shaped keys, because that is what `_apply_pipeline_info` takes
        # and what `guess.channel_tokens().as_pipeline_info()` below already
        # produces. Spelled `filename_sep` / `filename_schema` — the flattened,
        # argparse-side names — these two were the only keys in the dict that
        # `_pinfo_flat` does not read, so they were dropped on the way in and
        # the scan applied its channel roles and silently kept the form's
        # default schema. A plate in any other layout then reached the run with
        # a schema the user was never prompted to correct, which is the exact
        # failure `_well_folder_stems_in` was added to prevent.
        #
        # Applied only when the inference calls itself *confident*, which it does
        # iff it placed both a well slot and a channel slot — its own module
        # docstring says a non-confident guess is advisory, and this is the
        # caller that has to honour that. A single-channel plate carries a
        # constant channel token, so no channel slot can be placed and the guess
        # comes back with `ignore` where the channel belongs; writing that into
        # the picker produces a schema the form's own validator rejects ("channel
        # must appear exactly once"), which `_validate_run_request` raises as a
        # blocking dialog. Picking such a folder made Run impossible until the
        # user noticed that one slot of the picker read `ignore`.
        info: dict = {}
        if guess.confident:
            info = {"separator": guess.separator, "schema": guess.schema_str}
        tokens = guess.channel_tokens() if guess.channels else None
        if tokens is not None:
            info.update(tokens.as_pipeline_info())
        if info:
            self._apply_pipeline_info(info)
        self._report_scan_guess(
            tokens, schema=guess.schema_str if guess.confident else "")

    def _report_scan_guess(self, tokens, *, schema: str = "") -> None:
        """State what the folder scan filled in, on the run dashboard.

        *schema* is the schema the scan actually applied, or ``""`` when it
        applied none. Both halves are named because both overwrite fields whose
        new values look no different from hand-entered ones — and because a line
        reading "schema guessed" whether or not a schema was written is how a
        scan that rewrote the form and a scan that left it alone came to be
        indistinguishable.
        """
        parts = [f"schema {schema}" if schema
                 else "schema not recognised, left as you set it"]
        if tokens is None:
            parts.append("channel tokens not recognised — check the Channel "
                         "Tokens rows")
        else:
            named = ", ".join(tokens.fluor_tokens) or "none"
            part = f"channels {named}"
            if tokens.nuclear_token:
                part += f"; segmentation channel {tokens.nuclear_token}"
            if tokens.phase_token:
                part += f"; phase {tokens.phase_token}"
            parts.append(part)
        msg = "Scanned folder: " + "; ".join(parts)
        if tokens is not None:
            msg += " — check before running"
        try:
            self._dashboard.set_status(msg)
        except Exception:
            swallowed()

