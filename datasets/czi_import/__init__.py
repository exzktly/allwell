"""Convert CZI — turn a Zeiss ``.czi`` plate scan into a dataset folder.

Zeiss acquisition software saves a whole plate scan either as one ``.czi``
holding every scene (well), mosaic tile (field of view), channel, Z-plane and
timepoint, or — its *Split Scenes* export — as a folder of one file per scene.
This page reads both; :mod:`pipeline.czi_set` is what makes a folder one plate. The pipeline wants the opposite: one 2-D TIFF per
(channel, well, FOV, timepoint), grouped per well. This feature is the
standalone converter that bridges the two — it reads the CZI's subblock
directory and metadata, maps each scene to a 96-well label, max-projects Z, and
writes ordinary per-plane TIFs into ``<destination>/in/``.

Keeping conversion separate from processing is deliberate, for the same reason
the OME-TIFF converter is a page of its own: converting first produces a normal
dataset folder, which the user then opens in Analyze and processes like any
other input, so every well is on disk before validation, background selection
and auto-threshold ever run.

What this converter decides that the OME-TIFF one does not is **where the well
labels come from**. A CZI plate scan normally names each scene after the well it
scanned, so the mapping is read out of the file and the user confirms it on the
plate; the grid is the fallback for a file that does not name its scenes. The
two are never mixed — a mapping half from the file and half from a grid is
right exactly where a user is most likely to spot-check it.

Module split (CLAUDE.md §3 suffixes):

* :mod:`pipeline.czi_read` — the ZISRAW parser: the layout, and one 2-D plane
  at a time. Qt-free and in ``pipeline/`` because it is pixel work with no GUI
  knowledge, like ``pipeline.ome_explode``.
* :mod:`pipeline.czi_set` — one or more files read as one plate, and the
  uniformity checks that let them be one.
* :mod:`pipeline.czi_explode` — the per-well explode: scenes and tiles onto
  well labels, Z max-projected, one atomic archive per well.
* :mod:`~datasets.czi_import.czi_import_compute` — Qt-free planning: resolve
  the page's settings against the layout into a
  :class:`~datasets.czi_import.czi_import_compute.ConversionPlan`, and own the
  resume marker.
* :mod:`~datasets.czi_import.apply` — Qt-free executor: calls
  :func:`pipeline.czi_explode.explode_czi` into ``<dest>/in`` with progress and
  cancellation, and formats the summary.
* :mod:`~datasets.czi_import.worker` — a thin ``QThread`` that runs planning +
  conversion off the UI thread, emitting progress.
* :mod:`~datasets.czi_import.dashboard` — the per-well plate map.
* :mod:`~datasets.czi_import.view` — the Qt builder (a source picker that takes
  a file or a folder, the "convert the whole folder" tick-box, the well-source
  choice, grid controls, live readout, status section).
* :mod:`~datasets.czi_import.controller` — wires the view to the worker and the
  "open in Analyze" hand-off.
"""
