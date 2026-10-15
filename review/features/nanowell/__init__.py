"""Nanowell arrays in Review: the per-nanowell tables a nanowell-mode run writes, and what is computed from them.

The data half (tracker Section D) lives here: :mod:`.collect` reads ``nanowells/<well>.csv`` and the grid
records, :mod:`.series_compute` joins each nanowell over time and computes the per-nanowell metrics,
:mod:`.summary_compute` summarises a well's nanowells and compares wells, and :mod:`.controller` resolves the
replicate groups and writes the long-format export. The page that draws them is Section V.
"""
