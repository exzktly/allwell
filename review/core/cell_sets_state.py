"""Saved cells: named, fixed lists of cells (tracker M4).

A cell set is what a lasso on Spatial or Multivariate saves: the cells inside
it, by their ``CellKey`` tuple, with the ``<well>_out.zip`` fingerprint of each
well at the moment it was saved. Nucleus ids are re-allocated when a well is
segmented again, so a set's cells in a re-segmented well no longer name the
cells that were drawn: ``stale`` counts them per well, and every use of the set
leaves them out and says so (``cell_sets.usable_cells``).

Users see these as **saved cells**; the word "population" is not used, because
``PopulationStore`` already owns it.

Qt-free: imports only stdlib (state-package import contract, ARCHITECTURE §5.9).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CellSet:
    """One saved set. ``cells`` are normalised ``(well, fov, tp, nucleus_id)`` tuples."""

    name: str
    cells: frozenset
    created: str = ""
    #: Where it was drawn, e.g. ``"lasso · Spatial map"``.
    source: str = ""
    #: What a reader needs to know about how it was drawn, e.g. that it was
    #: drawn on a sample: "drawn on 2,500 sampled cells of 61,000".
    note: str = ""
    #: Each well's ``<well>_out.zip`` fingerprint when the set was saved.
    fingerprints: dict = field(default_factory=dict)


@dataclass
class CellSetsState:
    sets: list = field(default_factory=list)
    #: Per set name: ``{well: n_cells}`` for the wells re-segmented since it
    #: was saved (fingerprint changed or unreadable). Computed at load.
    stale: dict = field(default_factory=dict)
    #: The one set whose cells are left out of every analysis, or ``None``.
    exclude: str | None = None
