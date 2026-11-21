"""The iPhone Duo is recognised by its device pixels, whatever display scale the desktop runs at.

``core.phone_layout.is_duo_resolution`` names the Duo by Apple's published resolutions (1878 x 2670 unfolded,
1398 x 2034 folded), which are device pixels. Qt reports a screen's available geometry in logical pixels, so at a
display scale of 125 % or 150 % the Duo's own desktop arrives as 1502 x 2088 or 1252 x 1732 (less a taskbar) —
sizes that hold the desk window, are no phone's proportions and name no Duo side — and phone layout was not
offered there. The callers now pass the screen's device pixel ratio and the comparison is made at
``logical x dpr``. The wrong implementation each case rejects is the logical comparison.

Rules and the startup re-entry only; the shell's own offer is driven in ``tests/test_phone_layout.py``
(``test_the_banner_offers_the_duo_phone_layout_under_display_scaling``). Behaviour, not layout, so CI runs it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QRect

from core import phone_layout
from ui import theme

_SCALES = [1.25, 1.5, 1.75, 2.0, 2.5]


def _logical(px, dpr, taskbar=0):
    w, h = px
    return int(w / dpr), int(h / dpr) - taskbar


@pytest.mark.parametrize("dpr", _SCALES)
@pytest.mark.parametrize("screen", [phone_layout.DUO_INNER_PX, phone_layout.DUO_OUTER_PX], ids=["inner", "outer"])
@pytest.mark.parametrize("sideways", [False, True], ids=["upright", "sideways"])
def test_the_duo_at_its_own_resolution_is_offered_phone_layout_at_any_display_scale(dpr, screen, sideways):
    w, h = screen if not sideways else screen[::-1]
    lw, lh = _logical((w, h), dpr, taskbar=48)
    assert phone_layout.is_phone_shaped(lw, lh, dpr) is True


@pytest.mark.parametrize("w,h,dpr", [(1502, 2088, 1.25), (1252, 1732, 1.5), (1118, 1579, 1.25)])
def test_the_scaled_duo_is_named_by_its_device_pixels(w, h, dpr):
    """The cases that the logical comparison missed, named on their own so the failure says which."""
    assert phone_layout.is_duo_resolution(w, h, dpr) is True
    assert phone_layout.is_duo_resolution(w, h) is False        # the same logical size at 100 % is no Duo


_IPADS = [(2160, 1620), (2360, 1640), (2388, 1668), (2420, 1668), (2732, 2048), (2752, 2064), (2266, 1488)]


@pytest.mark.parametrize("w,h", _IPADS)
@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5])
def test_an_ipad_is_still_no_duo_at_any_display_scale(w, h, dpr):
    """Multiplying back must not make an iPad's device pixels read as the Duo's."""
    for ww, hh in ((w, h), (h, w)):
        lw, lh = _logical((ww, hh), dpr)
        assert phone_layout.is_duo_resolution(lw, lh, dpr) is False


class _Screen:
    def __init__(self, w, h, dpr):
        self._r, self._dpr = QRect(0, 0, w, h), dpr

    def availableGeometry(self):
        return self._r

    def geometry(self):
        return self._r

    def devicePixelRatio(self):
        return self._dpr


class _App:
    def __init__(self, w, h, dpr):
        self._s = _Screen(w, h, dpr)

    def primaryScreen(self):
        return self._s


def test_startup_reenters_phone_layout_on_the_duo_at_150_percent():
    """``allwell._phone_layout_at_startup`` is the other caller: left on at the Duo, the next launch on the same
    1878 x 2670 desktop at 150 % must open in phone layout."""
    import allwell
    from core.app_settings import app_settings
    s = app_settings()
    s.setValue(phone_layout.KEY_ON, True)
    s.setValue(phone_layout.KEY_TEXT_SIZE, phone_layout.DEFAULT_TEXT_SIZE)
    s.sync()
    try:
        # Sideways, less a taskbar, the logical size is 1780 x 1204: 1.478 : 1, past FOLD_MAX_ASPECT, so the
        # near-square rule that keeps phone layout on cannot take it by shape. Only its device pixels name it.
        assert not phone_layout.is_fold_shaped(1780, 1204)
        assert allwell._phone_layout_at_startup(_App(1780, 1204, 1.5)) is True
    finally:
        s.setValue(phone_layout.KEY_ON, False)
        s.sync()
        theme.apply_ui_scale(1.0)
        theme.set_touch_unit(None)
        phone_layout.set_on(False)
