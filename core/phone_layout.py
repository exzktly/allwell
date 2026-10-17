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

#: Text sizes offered in the ☰ menu. 1.0 is the desktop size.
PRESETS: tuple[float, ...] = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5)

#: Stored in place of a preset to mean "work it out from the screen".
AUTO = 0.0

#: Auto aims for this many logical pixels across the screen's short side. A
#: phone held upright is about 390–430 points wide; laying the app out for
#: ~600 keeps a plot legible after the remote viewer shrinks the desktop to the
#: phone, while leaving the one-column layout enough width for a control row.
AUTO_TARGET_SHORT_SIDE = 600

#: The desktop window's minimum size (``allwell.py``). A screen smaller than
#: this cannot hold the desktop layout, which is one of the ways a screen
#: reads as phone-shaped.
DESK_MIN_W, DESK_MIN_H = 1100, 800

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
    return height > width or width < DESK_MIN_W or height < DESK_MIN_H


def auto_text_size(width: int, height: int) -> float:
    """The preset Auto picks for a screen of this available size."""
    short = min(width, height)
    if short <= 0:
        return 1.0
    return snap_to_preset(short / AUTO_TARGET_SHORT_SIDE)


def snap_to_preset(factor: float) -> float:
    """The nearest offered preset to *factor* (ties go to the smaller)."""
    return min(PRESETS, key=lambda p: (abs(p - factor), p))


def resolve_text_size(stored: float, width: int, height: int) -> float:
    """The scale to apply: the stored preset, or Auto's pick for this screen."""
    if stored == AUTO:
        return auto_text_size(width, height)
    return snap_to_preset(stored)


def plot_factor(text_scale: float) -> float:
    """The DPI factor for plots at a given text scale: its square root.

    Plots in phone layout already span the screen's full width, and their
    renderers lay out axes with fixed margins, so scaling their type by the
    whole text factor overflows them; the square root keeps them legible after
    the remote viewer shrinks the desktop, without clipping a title.
    """
    return max(1.0, text_scale) ** 0.5


def parse_text_size(value) -> float:
    """A stored text size read back from settings, which may hand back a str.

    Anything unreadable, or outside the offered presets, means Auto.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return AUTO
    return f if f in PRESETS else AUTO


def parse_bool(value) -> bool:
    """A stored flag read back from settings (``"true"`` on some backends)."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def text_size_label(stored: float) -> str:
    """How a stored text size reads in the menu."""
    if stored == AUTO:
        return "Auto"
    return f"{round(stored * 100)} %"
