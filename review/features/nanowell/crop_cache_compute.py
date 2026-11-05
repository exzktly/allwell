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
