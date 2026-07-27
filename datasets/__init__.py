"""Datasets pane — dataset-level operations that sit beside Analyze and Review.

The Datasets pane is the third top-level mode of the app. Unlike Review it
carries no well picker; its left sidebar is a plain page navigator, one entry
per feature. The first (and, for now, only) feature is *Concatenate datasets*
(:mod:`datasets.concatenate`), which appends one dataset's timepoints after
another's.

The heavy planning/rewriting logic lives in Qt-free modules so it is unit-
testable without building the app; the Qt views are thin builders on top.
"""
