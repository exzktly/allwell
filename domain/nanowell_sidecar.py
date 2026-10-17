"""What the sidecar's ``nanowell`` block says about how much of a plate it covers.

Stdlib only, and in ``domain/`` because two panes must agree about it: Datasets'
Merge Plates writes the marker, and Review reads it to decide the image-border
switch's default (``review.domain.qc_params.default_exclude_border_cells``).

A nanowell run writes the block (``pipeline.nanowell_task.record_nanowell_info``)
and an ordinary run never does, so its presence used to mean "this whole plate
is a nanowell run". Merge Plates breaks that: merging a nanowell plate with an
ordinary one keeps the block, because the Nanowells page needs the scheme and
settings for the wells that have tables, while the other plate's wells are
ordinary wells. The merge says so with :data:`MIXED_WITH_ORDINARY_KEY`, so a
default that is right for a nanowell tile — keep the cells a tile edge cuts,
since a tile edge is not a well edge — is not applied to the ordinary wells,
whose cells the image edge does cut.
"""

from __future__ import annotations

#: Set (true) on a merged plate's ``nanowell`` block when part of the plate came
#: from an ordinary run. Carried by any later Subset, Concatenate or Merge Plates
#: of that plate; dropped when Analyze rewrites the sidecar, which is right,
#: since a fresh nanowell run analyses every well as a nanowell well.
MIXED_WITH_ORDINARY_KEY = "mixed_with_ordinary_plate"


def is_partly_ordinary(block) -> bool:
    """Whether a ``nanowell`` block says part of its plate is an ordinary run."""
    return isinstance(block, dict) and bool(block.get(MIXED_WITH_ORDINARY_KEY))
