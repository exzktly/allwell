"""Concatenate datasets — append one dataset's timepoints after another's.

Two datasets of the *same plate*, imaged in separate sessions, are stitched into
one longer timelapse: dataset 2's timepoints are relabelled to continue after
dataset 1's last timepoint (its first lands at ``ds1_last + ds1_interval``,
rendered in dataset 1's own label format), and the union of both datasets'
wells is written to a fresh destination folder.

A measurement row's timepoint is carried by three values, not one — the label
string, a numeric ``timepoint_hours`` column, and a ``filename`` column — and a
zip member's timepoint lives in its name. All four are kept in lock-step for
dataset-2 rows/members; a merge that touches only the label string leaves the
numeric column (what the viewer actually plots from) unshifted.

Module split (CLAUDE.md §3 suffixes):

* :mod:`~datasets.concatenate.concatenate_compute` — pure planning: read +
  validate both ``pipeline_info.json``, detect dataset 1's timepoint format,
  compute the offset, enumerate wells, and produce a
  :class:`~datasets.concatenate.concatenate_compute.MergePlan`. Also owns the
  structural filename-rewrite gate and the in-progress-marker restart check.
* :mod:`~datasets.concatenate.apply` — Qt-free executor: copies/repacks
  per-well inputs and QC zips, remaps CSV rows (timepoint, timepoint_hours,
  filename), carries over ``cell_overrides.json``, verifies the merged output
  before writing the sidecar, and cleans up on a resumed restart.
* :mod:`~datasets.concatenate.worker` — a thin ``QThread`` wrapper that runs
  planning + execution off the UI thread, emitting progress for the status
  window.
* :mod:`~datasets.concatenate.view` — the Qt builder for the main viewer area
  (three folder fields + a status window).
* :mod:`~datasets.concatenate.controller` — wires the view to the worker,
  including the destructive-restart confirmation dialog.
"""
