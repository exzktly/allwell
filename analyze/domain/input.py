"""Analyze-pane input handling, split from ``analyze/tab.py`` for line budget.

``AnalyzeTab`` mixes in :class:`AnalyzeInputMixin` for three members that don't
belong in the tab's run/log core:

* ``_resolve_run_dirs_for_runner`` — the adapter handed to
  :class:`services.pipeline_runner.PipelineRunner`, which resolves the selected
  folder into ``(input_dir, output_dir)`` — refusing loose images and per-well
  folders — and announces the resolved wells. Analyze only ever processes **folders**: a packed acquisition file
  is converted to a dataset folder first, in the Datasets → *Convert OME-TIFF*,
  *Convert CZI* or *Convert ND2* page, so no de-interleave happens here.
* ``_packed_input_hint`` — a guard that spots a raw ``.ome.tif``, ``.czi`` or
  ``.nd2`` selected as input and points the user at the converter for it
  instead of trying to process the file. The suffix table it reads is the one
  place that decides which page converts what, so a format is added there and
  nowhere else.
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
    detect_packed_input,
    iter_well_tokens,
    plan_input_layout,
    resolve_input_output,
)
from services.run_detect import scan_pipeline_info
from util.schema_infer import infer_from_dir
from core.safe import swallowed


class AnalyzeInputMixin:
    """Input-folder resolution + the packed-file redirect for the Analyze pane."""

    def _packed_input_hint(self) -> str | None:
        """Return a message that must block the run, or ``None`` to let it start.

        Analyze processes folders; a packed acquisition file — a packed
        OME-TIFF, a Zeiss CZI or a Nikon ND2 — must be converted in the
        Datasets pane first,
        so one selected here earns that converter's own message. The message
        comes back from :func:`detect_packed_input` rather than being chosen
        here: which page to send the user to is decided by the suffix, and
        naming the wrong converter sends them to one that will refuse the file.

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
            detected = detect_packed_input(Path(raw_str))
        except Exception as exc:
            # Deliberately broad, and deliberately not silent: this runs from
            # the Run button's own slot, so a type not listed here would kill
            # the click rather than report anything (see the Manage-Remotes
            # dead-button defect this audit found next door).
            return (f"Could not inspect the selected input:\n{raw_str}\n\n"
                    f"{type(exc).__name__}: {exc}\n\n"
                    "Analyze checks here whether the folder holds a packed "
                    "acquisition file, which has to be converted in Datasets "
                    "first. The check did not complete, so the run is not "
                    "started.")
        return detected.message if detected is not None else None

    def _resolve_run_dirs_for_runner(self, opts: dict, log_q: queue.Queue) -> tuple | None:
        """Adapter passed to ``PipelineRunner.start``.

        Resolves ``opts['raw']`` into ``(input_dir, output_dir)`` via
        :func:`resolve_input_output`, which refuses loose images and per-well
        folders by name.
        """
        try:
            log_q.put(("zipper_start", self._expected_well_count(opts)))
            input_dir, output_dir = resolve_input_output(opts["raw"])
            # Every resolved well, so none is missing from the plate/rail
            # before the pipeline reports on it.
            for tok in iter_well_tokens(input_dir):
                log_q.put(("zipper_well", tok))
            log_q.put(("zipper_done", None))
            return input_dir, output_dir
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
            self._set_schema_detection(
                "That folder could not be read, so no layout was detected.",
                problem=True)
            self._report_scan_guess(None)
            return
        if guess is None:
            # Nothing recognisable, which is not the same as nothing found: the
            # modal token count decides the layout, so this is also what a
            # folder whose unparseable names outnumber its parseable ones
            # returns. Either way the form is left exactly as the user set it,
            # and saying so is the difference between a scan that declined and
            # a scan that silently rewrote nothing.
            self._set_schema_detection(
                "Couldn't read a layout from these filenames — your settings "
                "below are unchanged.", problem=True)
            # The dashboard has to be told too. Both refusals used to return
            # before this, so a scan that found nothing left the previous
            # scan's line standing — and once the picker gained a readout of
            # its own, the two surfaces openly contradicted each other: the
            # dashboard naming a schema it had applied, the readout saying
            # none was detected. Visible in the rendered audit, not in any
            # test, because no test reads two widgets at once.
            self._report_scan_guess(None)
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
        #
        # The rule itself is `services.run_detect.scan_pipeline_info`, shared
        # with the run-time detection a queued job can ask for, so the button
        # here and a queued run cannot disagree about what a scan may write.
        info, tokens = scan_pipeline_info(guess)
        if info:
            self._apply_pipeline_info(info)
            # A scan leaves the segmentation channel out of the fluorescent list; with label-free already chosen
            # that channel would be dropped from the run (``seg_params._watch_labelfree_handover``).
            self._settle_labelfree_handover(hand_over=True)
        # Beside the fields it just wrote, not only on the dashboard: the
        # dashboard line scrolls away and these two fields keep values that look
        # hand-entered for as long as the form is open.
        if guess.confident:
            self._set_schema_detection(guess.summary(), note=guess.shortfall())
        else:
            self._set_schema_detection(
                f"{guess.summary()} No well and channel pair was identified, "
                f"so your settings below are unchanged.", problem=True)
        self._report_scan_guess(
            tokens, schema=guess.schema_str if guess.confident else "",
            shortfall=guess.sampled - guess.matched)

    def _report_scan_guess(self, tokens, *, schema: str = "",
                           shortfall: int = 0) -> None:
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
            parts.append("channels not recognised — check the rows under "
                         "Channels")
        else:
            named = ", ".join(tokens.fluor_tokens) or "none"
            part = f"channels {named}"
            if tokens.nuclear_token:
                part += f"; segmentation channel {tokens.nuclear_token}"
            if tokens.phase_token:
                part += f"; phase {tokens.phase_token}"
            parts.append(part)
        # The dashboard asserted a clean scan while the picker's readout
        # qualified it, so a reader who took the top line and skipped the
        # sidebar started a run believing every file was in play.
        if shortfall > 0:
            parts.append(f"{shortfall} file(s) will not match")
        msg = "Scanned folder: " + "; ".join(parts)
        if tokens is not None:
            msg += " — check before running"
        try:
            self._dashboard.set_status(msg)
        except Exception:
            swallowed()

    def _refresh_output(self, *, immediate: bool = False) -> None:
        """Show where the run will read from and write to, and what it will do.

        Every word of this comes from ``plan_input_layout`` — the same function
        ``resolve_input_output`` executes when Run is pressed. The readout used
        to re-implement the layout rules with a looser idea of what counts as
        well content, so a folder holding ``in/Plate1_A1.zip`` displayed
        "using as input" and then failed the run outright.
        """
        if self._suspend_output_refresh:
            return
        raw_str = self._input_edit.text().strip()
        pending = getattr(self, "_pending_input", None)
        if pending is not None and pending[0] != raw_str:
            # The user replaced the queued job's output with a real folder.
            self._pending_input = pending = None
            self._sync_run_for_pending()
        if pending is not None:
            # A queued job's output, not written yet: planning it would only
            # say the folder does not exist. Say whose output it is instead.
            self._output_lbl.setText("—")
            self._output_row.setVisible(False)
            self._set_status(self._layout_lbl, pending[1], quiet="")
            self._refresh_folder_banners(None, immediate=immediate)
            return
        if not raw_str:
            self._output_lbl.setText("—"); self._set_status(self._layout_lbl, "", quiet="")
            self._output_row.setVisible(False)
            self._refresh_folder_banners(None, immediate=immediate)
            return
        plan = plan_input_layout(Path(raw_str))
        self._output_lbl.setText(
            str(plan.output_dir) if plan.output_dir is not None else "—")
        self._output_row.setVisible(plan.output_dir is not None)
        self._set_status(self._layout_lbl, plan.message, error=not plan.runnable, quiet="")
        # The same planner that names the output folder is what makes a pre-run
        # report possible at all: it resolves that path without writing
        # anything, so what an earlier run left there can be read while the
        # form is still being filled in.
        self._refresh_folder_banners(plan, immediate=immediate)
