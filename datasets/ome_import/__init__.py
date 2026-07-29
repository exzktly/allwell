"""Convert OME-TIFF — de-interleave a packed OME-TIFF into a dataset folder.

Some microscopes save a whole plate scan as a single OME-TIFF holding every
stage position, channel, Z-plane and timepoint. The pipeline, by contrast, wants
one 2-D TIFF per (channel, well, FOV, timepoint) grouped per well. This feature
is the standalone converter that bridges the two: it reads the OME metadata,
maps each stage position to a 96-well label (serpentine order + the imaged grid),
max-projects Z, and writes ordinary per-plane TIFs into ``<destination>/in/``.

Keeping conversion separate from processing is deliberate. When de-interleaving
ran *inside* an Analyze run, the wells only existed mid-run, so pre-run checks
that look for a well (an ``empty_well`` background, control-well auto-threshold)
failed against the raw ``.ome.tif``. Converting first produces a normal dataset
folder, which the user then opens in Analyze and processes like any other input —
every well is on disk before validation and correction ever run.

Module split (CLAUDE.md §3 suffixes):

* :mod:`~datasets.ome_import.ome_import_compute` — Qt-free planning: read the OME
  layout and validate the well-grid / FOVs / frame-order into a
  :class:`~datasets.ome_import.ome_import_compute.ConversionPlan`.
* :mod:`~datasets.ome_import.apply` — Qt-free executor: calls
  :func:`pipeline.ome_explode.explode_ome_tiff` into ``<dest>/in`` with progress
  and cancellation, and formats the summary.
* :mod:`~datasets.ome_import.worker` — a thin ``QThread`` that runs planning +
  conversion off the UI thread, emitting progress.
* :mod:`~datasets.ome_import.view` — the Qt builder (source/destination pickers,
  grid controls, live readout, progress + log).
* :mod:`~datasets.ome_import.controller` — wires the view to the worker and the
  "open in Analyze" hand-off.
"""
