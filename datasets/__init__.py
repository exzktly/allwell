"""Datasets pane — dataset-level operations that sit beside Analyze and Review.

The Datasets pane is the third top-level mode of the app. Unlike Review it
carries no well picker; its left sidebar is a plain page navigator, one entry
per feature. Each feature is a standalone package registered in
``datasets/tab.py::_FEATURES``: *Convert OME-TIFF* (:mod:`datasets.ome_import`)
de-interleaves a packed OME-TIFF into a dataset folder; *Concatenate datasets*
(:mod:`datasets.concatenate`) appends one dataset's timepoints after another's;
*Validate & Repair* (:mod:`datasets.validate`) reports and fixes a dataset's
metadata after a partial or crashed Analyze run; *Subset / Split*
(:mod:`datasets.subset`) copies a filtered slice of a dataset to a new folder;
*Zip loose folder* (:mod:`datasets.import_loose`) groups loose TIFs into
per-well zips; *Merge plates* (:mod:`datasets.merge_plates`) joins two
different plates along the well axis into one dataset.

The heavy planning/rewriting logic lives in Qt-free modules so it is unit-
testable without building the app; the Qt views are thin builders on top.
Helpers shared across features (dataset-folder resolution, well-container IO,
schema-safe filename rewriting) live in :mod:`datasets._common`.
"""
