"""Hydrating the Analyze form from a queued job's spec.

The Job Queue lets a queued job be edited: the row's *Edit…* action populates
the tool's own page, the user adjusts it, and Save writes the page back over
the job's stored spec. Every Datasets tool gets that for free, because its
``show_job(spec)`` is a genuine inverse of its ``collect_spec()`` — the two
read and write the same handful of keys.

Analyze is the exception. Its ``show_job`` (``analyze/run_spec.py``) sets only
the input-folder field, and deliberately so: it exists for *preview*, and the
queue re-derives a full ``RunSpec`` from the persisted JSON at dequeue time
regardless, so the live form only has to look right. That is fine for a
preview and catastrophic for an edit — ``collect_spec()`` on save would write
the form's forty-odd current option values over the job's stored ones, none of
which the user had asked to change.

:meth:`AnalyzeJobEditMixin.hydrate_job` is the real inverse, used by the edit
session only. ``show_job`` keeps its shallow preview behaviour.

Most of the work is already done by
``analyze.pinfo.AnalyzePipelineInfoMixin._apply_pipeline_info``, which
restores nearly the whole form from argparse-style keys — and a job's
``spec["opts"]`` is argparse-style, being what ``build_pipeline_args``
consumes. What it is not is *identical*: the sidecar that ``_apply_pipeline_info``
was written for records some options under different names, some with the
opposite polarity, and some not at all. :func:`_opts_as_pinfo_keys` documents
and bridges each of those, and the fields living outside ``opts`` entirely
(the remote, and the cleanup flags that ride on ``RunSpec`` itself) are
restored here directly.

The guard that keeps this honest is ``tests/test_analyze_job_edit.py``: a
round trip of ``collect_spec()`` → ``hydrate_job()`` → ``collect_spec()`` over
a fully-populated form. Any option added to ``_collect_run_options`` without a
counterpart here fails it.
"""

from __future__ import annotations

from core.safe import swallowed


def _opts_as_pinfo_keys(opts: dict) -> dict:
    """Translate a job spec's ``opts`` into the keys ``_apply_pipeline_info`` reads.

    Five options are spelled differently by the two sides, and each is a silent
    wrong value rather than a crash if left unbridged:

    * ``raw`` is the form's input folder; the sidecar calls it ``input_dir``.
    * ``auto_threshold`` is positive here and recorded as its negative,
      ``no_auto_threshold``, in a sidecar (the pipeline takes the flag that
      turns the pass *off*).
    * ``auto_threshold_method`` is explicit here. A sidecar has no such key, so
      ``_apply_pipeline_info`` infers the method from whether both control
      wells are named — which is right for a sidecar and wrong for a spec that
      states the method itself. Restored explicitly by the caller, after.
    * ``stardist_prob_thresh`` / ``stardist_nms_thresh`` are raw text here,
      blank meaning "the model's own default". ``_restore_stardist_thresholds``
      expects the pipeline's negative sentinel for that, and skips a value it
      cannot read as a float — so a blank would leave whatever the field
      already held. Also restored explicitly by the caller.

    * ``measure_focus`` is positive here and recorded as its negative,
      ``no_focus_metric``, in a sidecar — the same shape as
      ``auto_threshold`` above, and for the same reason: the pipeline takes
      the flag that turns the measurement *off*. Unbridged, a queued job that
      switched per-cell focus off came back with it on, and the run it then
      produced would carry a column its author had declined. ``focus_min`` is
      spelled the same on both sides and needs no bridge; a blank one round
      trips as a blank because ``_apply_focus_settings`` clears the box on a
      stated zero rather than skipping it.

    A sixth, ``allow_low_disk``, is spelled the same on both sides and still
    has to be restored by the caller: ``_apply_pipeline_info`` refuses it on
    purpose, because a sidecar describes some folder's last run and must not
    re-arm a safety override for the next one. A queued job is a decision the
    user made for that job, so it does come back.
    """
    d = dict(opts)
    raw = d.get("raw")
    if raw:
        d["input_dir"] = str(raw)
    if "auto_threshold" in d:
        d["no_auto_threshold"] = not bool(d["auto_threshold"])
    if "run_check" in d:
        d["no_run_check"] = not bool(d["run_check"])
    if "measure_focus" in d:
        d["no_focus_metric"] = not bool(d["measure_focus"])
    return d


class AnalyzeJobEditMixin:
    """Mixed into ``AnalyzeTab``; every method binds to that instance."""

    def hydrate_job(self, spec: dict) -> list[str]:
        """Populate the whole form from a queued job's spec.

        The inverse of ``AnalyzeRunMixin.collect_spec``. Unlike ``show_job``,
        which only makes the page *look* like the job, this makes the page
        *be* the job — so collecting it straight back afterwards reproduces
        the same spec.

        Returns anything it could not restore faithfully, for the caller to
        put in front of the user. There is one such case and it matters: a
        job naming a remote this install no longer has. Left silent, saving
        the edit would rewrite the job to run on whichever machine happened to
        be selected.
        """
        opts = spec.get("opts") or {}
        if opts:
            self._apply_pipeline_info({"execution_options": _opts_as_pinfo_keys(opts)})
            self._hydrate_opts_without_sidecar_equivalents(opts)
        return self._hydrate_run_spec_fields(spec)

    def _hydrate_opts_without_sidecar_equivalents(self, opts: dict) -> None:
        """The ``opts`` keys ``_apply_pipeline_info`` cannot restore correctly.

        See :func:`_opts_as_pinfo_keys` for why each one is here. This runs
        *after* ``_apply_pipeline_info`` precisely so it overrides what that
        function inferred.
        """
        method = opts.get("auto_threshold_method")
        combo = getattr(self, "_auto_threshold_method_cb", None)
        if method and combo is not None:
            idx = combo.findData(str(method))
            if idx >= 0:
                combo.setCurrentIndex(idx)

        # Blank is a meaningful value for both threshold fields ("use the
        # model's own default"), which is why these are set from the text
        # rather than left to _restore_stardist_thresholds' float parse.
        for attr, key in (("_stardist_prob_thresh_edit", "stardist_prob_thresh"),
                          ("_stardist_nms_thresh_edit", "stardist_nms_thresh")):
            widget = getattr(self, attr, None)
            if widget is not None and key in opts:
                widget.setText(str(opts[key] or "").strip())

        batch = getattr(self, "_remote_batch_size_edit", None)
        if batch is not None and "remote_batch_size" in opts:
            batch.setText(str(opts["remote_batch_size"] or ""))

        # Blank is a value for these two as well, and _apply_pipeline_info is
        # set-only (`if scratch and ...`, `if in_dir:`) — so hydrating a job
        # with no scratch dir over a form that has one left the previous job's
        # path in the field and collected it straight back into this job.
        for attr, key in (("_scratch_edit", "scratch_dir"), ("_input_edit", "raw")):
            widget = getattr(self, attr, None)
            if widget is not None and key in opts and not opts[key]:
                widget.setText("")
        if "scratch_dir" in opts:
            self._scratch_user_set = bool(opts["scratch_dir"])

        # ``allow_low_disk`` is the one option ``_apply_pipeline_info``
        # deliberately declines to restore: a sidecar is a description of some
        # folder's last run, and re-arming a safety override from one would
        # disarm the disk check on a volume the user never measured. A queued
        # job is the opposite — the user set that box for this job, when they
        # queued it — so editing the job has to show it back, or saving the
        # edit silently re-arms the check they turned off.
        box = getattr(self, "_allow_low_disk_cb", None)
        if box is not None and "allow_low_disk" in opts:
            box.setChecked(bool(opts["allow_low_disk"]))

    def _hydrate_run_spec_fields(self, spec: dict) -> list[str]:
        """The ``RunSpec`` fields that live beside ``opts``, not inside it.

        ``output_filter`` and ``selective_channels`` are deliberately absent:
        ``_collect_run_spec`` writes them as constants with no widget behind
        them, so there is nothing on the form to restore them into.
        """
        problems: list[str] = []
        remote = getattr(self, "_remote_cb", None)
        if remote is not None:
            wanted = spec.get("remote_name")
            idx = remote.findData(wanted)
            if idx >= 0:
                remote.setCurrentIndex(idx)
            elif wanted:
                # The remote was deleted from remotes.toml, or renamed. Falling
                # through silently would leave whatever was selected and save
                # the job onto a different machine than it named.
                problems.append(
                    f"This job runs on “{wanted}”, which is no longer a "
                    f"configured remote — saving will move it to "
                    f"“{remote.currentText()}”.")

        cleanup = getattr(self, "_cleanup_remote_cb", None)
        if cleanup is not None:
            cleanup.setChecked(str(spec.get("cleanup_remote") or "none") == "all")

        dry_run = getattr(self, "_dry_run_cleanup_cb", None)
        if dry_run is not None:
            dry_run.setChecked(bool(spec.get("dry_run_cleanup")))

        # Set whether or not the key is present: a job queued before run-time
        # detection existed has none, and means "no", so hydrating it over a
        # form where the box was ticked must untick it rather than save the
        # previous job's choice into this one.
        detect = getattr(self, "_auto_detect_cb", None)
        if detect is not None:
            detect.setChecked(bool(spec.get("auto_detect")))

        # The remote-only parameter group's visibility is driven by the combo
        # and the cleanup checkbox; both were just set programmatically, which
        # does not always fire the signals that keep the group in step.
        refresh = getattr(self, "_refresh_remote_controls", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                swallowed()
        return problems
