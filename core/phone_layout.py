"""Phone layout: whether it is on, and the rules that size it.

All-Well is often reached from a phone over remote desktop (RealVNC resizes the
Windows desktop to the phone's shape, then scales that desktop down to fit the
screen). Phone layout is the one-column, larger-text arrangement for that case;
``ui/phone_shell.py`` builds and drives it.

This module is the part every pane may read without importing the shell or each
other: one process-wide flag and the Qt-free rules behind it. It is a module
rather than an attribute on a window because the window a pane holds is not the
one that would own it (``ctx.window`` is the Review widget, not the main window
— the trap ``core/app_settings.py`` describes), and because the panes that
must react to it (the rail toggle, Split View, the plot canvases) sit several
packages away from the button that changes it.
"""

from __future__ import annotations

from collections.abc import Callable

#: Phone layout sizes everything in *phone points*. RealVNC shrinks the whole
#: desktop to the phone's width, so one logical pixel on the desktop lands on
#: the phone as ``390 / short side`` points. ``phone_unit`` is the inverse: how
#: many logical pixels one phone point takes, taking the phone as 390 pt wide —
#: the median (iPhone 12–16 are 390–393 pt; Android phones 360–412 dp). The
#: short side does not change when the phone is turned, so neither does the
#: unit: a button is the same size on the phone in either orientation.
PHONE_REFERENCE_WIDTH_PT = 390

#: Touch sizes, in phone points. 44 pt is the smallest target a thumb hits
#: reliably (Apple's minimum; Material's 48 dp is the same physical size on a
#: 360-dp phone); bars, rows and menu items take 48. Targets sit at least 8 pt
#: apart. The one exception is a 96-well plate, which cannot give 12 wells
#: 44 pt each on a 390-pt screen; its wells are as large as the width allows.
TOUCH_MIN_PT = 44
TOUCH_ROW_PT = 48
TOUCH_GAP_PT = 8
TOP_BAR_PT = 52

#: Body text on the phone, in points, at the Medium text size.
BODY_PT = 15

#: The text sizes on offer, and how much each multiplies the type by. Text
#: only: touch targets are floors set by the unit, and never shrink with it.
TEXT_SIZES = ("S", "M", "L")
TEXT_SIZE_FACTOR = {"S": 0.87, "M": 1.0, "L": 1.13}
TEXT_SIZE_LABEL = {"S": "Small", "M": "Medium", "L": "Large"}
DEFAULT_TEXT_SIZE = "M"

#: The desktop window's minimum size (``allwell.py``). A screen smaller than
#: this cannot hold the desktop layout, which is one of the ways a screen
#: reads as phone-shaped.
DESK_MIN_W, DESK_MIN_H = 1100, 800

#: A screen this much wider than tall, and shorter than the second figure, is a
#: phone on its side rather than a monitor (see ``is_phone_shaped``).
PHONE_MIN_ASPECT = 1.95
PHONE_MAX_SHORT_SIDE = 1300

#: ``core.app_settings`` keys.
KEY_ON = "ui/phone_layout"
KEY_TEXT_SIZE = "ui/phone_text_size"

_state = {"on": False}
_listeners: list[Callable[[bool], None]] = []


def is_on() -> bool:
    """True while the window is in phone layout."""
    return bool(_state["on"])


def set_on(on: bool) -> None:
    """Record the layout and tell every listener. Only the shell calls this."""
    on = bool(on)
    if on == _state["on"]:
        return
    _state["on"] = on
    for fn in list(_listeners):
        fn(on)


def add_listener(fn: Callable[[bool], None]) -> None:
    """Call ``fn(on)`` whenever phone layout turns on or off."""
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn: Callable[[bool], None]) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def is_phone_shaped(width: int, height: int) -> bool:
    """Does a screen of this available size look like a phone?

    Upright (taller than wide), or too small in either direction to hold the
    desktop window. A phone held sideways at its full physical resolution is
    indistinguishable from a wide monitor by size alone, so it reads as
    desk-shaped; the toggle is how the user says otherwise.
    """
    if width <= 0 or height <= 0:
        return False
    if height > width or width < DESK_MIN_W or height < DESK_MIN_H:
        return True
    # Sideways at full resolution a phone is the size of a monitor, but not
    # its shape: phones are 19.5:9 or longer (2.17), monitors 16:9 (1.78) or
    # 16:10. Only an ultrawide 21:9 at 1080 lines tall reads as a phone too,
    # which costs it no more than an offer it can dismiss.
    return width / height >= PHONE_MIN_ASPECT and height < PHONE_MAX_SHORT_SIDE


def phone_unit(width: int, height: int) -> float:
    """Logical pixels per phone point on a screen of this size (at least 1)."""
    short = min(width, height)
    if short <= 0:
        return 1.0
    return max(1.0, short / PHONE_REFERENCE_WIDTH_PT)


def type_scale(unit: float, size: str, desk_body_px: int) -> float:
    """The factor the type tokens take in phone layout.

    Body text becomes ``BODY_PT`` phone points (times the text size), from a
    desk body of *desk_body_px*. Not snapped to steps: the unit is whatever the
    screen makes it, and rounding it would make a 1080-wide desktop's text
    one size and a 1170-wide one's the same.
    """
    return BODY_PT * unit * TEXT_SIZE_FACTOR.get(size, 1.0) / desk_body_px


def touch_px(unit: float, points: float) -> int:
    """*points* phone points, in logical pixels."""
    return round(points * unit)


def plot_dpi_factor(unit: float, base_dpi: float = 100.0) -> float:
    """How much to raise a figure's DPI so a matplotlib point is a phone point.

    A figure draws a point as ``dpi / 72`` pixels; a phone point is *unit*
    pixels. So the DPI wanted is ``72 × unit``, against the ``base_dpi`` the
    figure was made with. Phone layout pairs this with constrained layout
    (``review/core/plot_style._apply_layout_engine``), since the renderers'
    fixed margins cannot hold text that large.
    """
    return max(1.0, 72.0 * unit / base_dpi)


def parse_text_size(value) -> str:
    """A stored text size read back from settings; anything else is Medium."""
    return value if value in TEXT_SIZES else DEFAULT_TEXT_SIZE


def parse_bool(value) -> bool:
    """A stored flag read back from settings (``"true"`` on some backends)."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)
