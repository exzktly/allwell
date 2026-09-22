"""Convert ND2 — turn a Nikon ``.nd2`` plate scan into a dataset folder.

NIS-Elements saves a whole plate scan as one ``.nd2`` holding every stage
position (well or field), channel, Z-plane and timepoint. The pipeline wants
the opposite: one 2-D TIFF per (channel, well, FOV, timepoint), grouped per
well. This feature is the standalone converter that bridges the two — it reads
the ND2's chunk map and metadata, maps each position to a 96-well label,
max-projects Z, and writes ordinary per-plane TIFs into ``<destination>/in/``.

Keeping conversion separate from processing is deliberate, for the same reason
the OME-TIFF and CZI converters are pages of their own: converting first
produces a normal dataset folder, which the user then opens in Analyze and
processes like any other input, so every well is on disk before validation,
background selection and auto-threshold ever run.

What this converter decides that the OME-TIFF one does not is **where the well
labels come from** — the same choice the CZI converter makes. A Nikon plate
scan normally names each stage position after the well it scanned, so the
mapping is read out of the file and the user confirms it on the plate; the grid
is the fallback for a file whose positions are unnamed. The two are never mixed
— a mapping half from the file and half from a grid is wrong exactly where a
user is most likely to spot-check it.

Where it is *simpler* than the CZI converter is that an ND2 is always one file.
Nikon has no equivalent of ZEN's *Split Scenes* export, so there is no set of
files to discover and no "convert the whole folder" question; and several
positions naming one well simply *are* that well's fields of view, so the
fields per well are something the file states rather than something the user
divides out.

Module split (CLAUDE.md §3 suffixes):

* :mod:`pipeline.nd2_read` — the ND2 reader: the layout, the frame times, and
  one 2-D plane at a time. Qt-free and in ``pipeline/`` because it is pixel
  work with no GUI knowledge, like :mod:`pipeline.czi_read`. It reads through
  the third-party ``nd2`` package; everything above it sees only
  :class:`~pipeline.nd2_read.Nd2Layout` and
  :class:`~pipeline.nd2_read.Nd2Reader`.
* :mod:`pipeline.nd2_explode` — the per-well explode: positions onto well
  labels, Z max-projected, one atomic archive per well.
* :mod:`~datasets.nd2_import.nd2_import_compute` — Qt-free planning: resolve
  the page's settings against the layout into a
  :class:`~datasets.nd2_import.nd2_import_compute.ConversionPlan`, and own the
  resume marker.
* :mod:`~datasets.nd2_import.apply` — Qt-free executor: calls
  :func:`pipeline.nd2_explode.explode_nd2` into ``<dest>/in`` with progress and
  cancellation, and formats the summary.
* The background ``QThread`` is
  :class:`datasets.core.packed_convert.PackedConverterWorker`, shared with the
  other packed-file converters and parameterised by this tool's
  :class:`~datasets.core.packed_convert.ConverterSpec`.
* :mod:`~datasets.nd2_import.dashboard` — the per-well plate map, a named
  subclass of the shared
  :class:`~datasets.core.converter_dashboard.ConverterDashboard`.
* :mod:`~datasets.nd2_import.view` — the Qt builder (the source picker, the
  well-source choice, grid controls, per-channel roles, live readout, status
  section).
* :mod:`~datasets.nd2_import.controller` — declares this tool's
  ``ConverterSpec`` and builds its page. Everything else — the run, the queue
  round-trip, the worker results, the "open in Analyze" hand-off — is
  :class:`~datasets.core.packed_convert.PackedConverterController`.
"""
