"""Concatenate datasets — append one dataset's timepoints after another's.

Two datasets of the *same plate*, imaged in separate sessions, are stitched into
one longer timelapse: dataset 2's timepoints are relabelled to continue after
dataset 1's last timepoint (its first lands at ``ds1_last + ds1_interval``), and
the union of both datasets' wells is written to a fresh destination folder.

Module split (CLAUDE.md §3 suffixes):

* :mod:`~datasets.concatenate.concatenate_compute` — pure, Qt-free planning:
  read + validate both ``pipeline_info.json``, compute the timepoint remap,
  enumerate wells, and produce a :class:`~datasets.concatenate.concatenate_compute.MergePlan`.
* :mod:`~datasets.concatenate.worker` — a ``QThread`` that executes a plan
  (copies files, rewrites timepoint tokens, merges CSVs / QC zips / sidecar),
  emitting progress for the status window.
* :mod:`~datasets.concatenate.view` — the Qt builder for the main viewer area
  (three folder fields + a status window).
* :mod:`~datasets.concatenate.controller` — wires the view to the worker.
"""
