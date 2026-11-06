"""The page's store of cut crops, bounded: ``(well, q, r) -> {timepoint: Crop}`` for at most ``CROPS_KEPT``
nanowells. A window's pop-out pages through hundreds of nanowells, and the store was never pruned. The oldest
inserted goes first; a key in ``protected`` (the selected nanowell and the pop-out's page) is never evicted.

Qt-free.
"""

from __future__ import annotations

from collections import OrderedDict

#: Nanowells' crops kept at most: about sixteen pages of the window's rows.
CROPS_KEPT = 400


class CropCache(OrderedDict):
    def __init__(self, *args, maxlen: int = CROPS_KEPT, **kwargs) -> None:
        self.maxlen = maxlen
        self.protected: set = set()
        super().__init__(*args, **kwargs)

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        excess = len(self) - self.maxlen
        if excess > 0:
            for old in [k for k in self if k not in self.protected][:excess]:
                del self[old]


class TimepointCropCache(CropCache):
    """The Distribution window's crops, cut **one timepoint at a time**: ``(well, q, r) -> {timepoint: Crop}`` for the
    timepoints cut so far, with :attr:`cut_at` saying which those are. The window's pop-out shows up to
    ``composite_compute.COMPOSITE_PAGE`` nanowells; cut at every timepoint in one pass, nothing showed until every
    tile of every timepoint had been decoded. Cut a timepoint at a time, the first timepoint shown fills in first,
    and a timepoint nobody looks at is never cut. A timepoint cut where a nanowell has no row leaves no crop, but is
    still in :attr:`cut_at`, so "cut, nothing there" is never mistaken for "not cut yet".

    Its own store, not ``NanowellState.crops``: that one holds each nanowell over **every** timepoint, and the site
    detail, the strip and the Extremes view read an entry there as complete."""

    def __init__(self, *args, **kwargs) -> None:
        self.cut_at: dict = {}
        super().__init__(*args, **kwargs)

    def add(self, found: dict, timepoints) -> None:
        """Merge *found* (``site -> {timepoint: Crop}``) and mark *timepoints* cut for every site in it."""
        for site, crops in found.items():
            site = tuple(site)
            merged = dict(self.get(site) or {})
            merged.update(crops)
            self.cut_at.setdefault(site, set()).update(str(tp) for tp in timepoints)
            self[site] = merged
        for gone in [k for k in self.cut_at if k not in self]:
            del self.cut_at[gone]

    def has(self, site, timepoint) -> bool:
        """Whether *site* has been cut at *timepoint* (whatever was found there)."""
        return str(timepoint) in self.cut_at.get(tuple(site), ())
