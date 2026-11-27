"""Where the Nanowells page decides what phone layout changes.

The page lays out differently on a phone: the crop strip under the site detail
wraps to its own shape. That decision is made here and nowhere else in the
feature, so the mode is read in
one place (``tests/test_phone_mode_stays_in_views.py``) and the controller and
the tab view only call. The renderer never reads the mode: what a narrow or short
figure needs (a footer broken to its width, a key clear of the titles) it derives
from the figure's own size in points, so a phone's figure and a small window's get
the same treatment.
"""

from __future__ import annotations

from core import phone_layout


def on_phone() -> bool:
    """Whether phone layout is on."""
    return phone_layout.is_on()


def strip_columns_for(fig, crops) -> int:
    """The crop strip's columns: one row at a desk (``0``), and in phone layout as many rows as make each crop
    largest in the strip's own figure (``crop_grid_compute.strip_columns``)."""
    from .crop_grid_compute import strip_columns

    if not crops or not on_phone():
        return 0
    width, height = fig.get_size_inches()
    return strip_columns(len(crops), width * 0.94, height * 0.78)
