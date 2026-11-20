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

from collections.abc import Callable, Iterator
from contextlib import contextmanager


#: Phone layout sizes everything in *phone points*. RealVNC shrinks the whole
#: desktop to the phone's width, so one logical pixel on the desktop lands on
#: the phone as ``390 / short side`` points. ``phone_unit`` is the inverse: how
#: many logical pixels one phone point takes, taking the phone as 390 pt wide —
#: the median (iPhone 12–16 are 390–393 pt; Android phones 360–412 dp). The
#: short side does not change when the phone is turned, so neither does the
#: unit: a button is the same size on the phone in either orientation.
PHONE_REFERENCE_WIDTH_PT = 390

#: The iPhone Duo, Apple's foldable, has two screens and both are near-square
#: — unlike every other phone, which is 16:9 (1.78) or longer, most 19.5:9
#: (2.17). Apple's published figures:
#:
#: * the inner screen, unfolded: 7.6 inches, 1878 × 2670 px at 430 ppi, 1.42 : 1;
#: * the outer screen, folded: 5.4 inches, 1398 × 2034 px at 460 ppi, 1.45 : 1.
#:
#: Apple publishes no point sizes; at the 3× scale of every recent iPhone the
#: pixels make the inner screen 626 × 890 points and the outer 466 × 678 (the
#: same figures come from the pixel densities: 1878 px / 430 ppi is 4.37
#: inches, and an iPhone's point is 1/143-1/153 inch). Those short sides are the
#: screens' reference widths, as 390 is a phone's.
DUO_INNER_PX = (1878, 2670)
DUO_OUTER_PX = (1398, 2034)
DUO_INNER_PT = 626
DUO_OUTER_PT = 466
#: Once phone layout is on, which of the two a near-square screen of some
#: other resolution is — RealVNC may give the desktop the screen's shape at a
#: resolution of its own — is read from its shape: the inner screen is 1.422 : 1 and the outer 1.455 : 1,
#: and this is the midpoint. A screen squarer still (a 4:3 or 5:4 monitor, an
#: Android fold's inner screen) reads as an unfolded inner screen.
DUO_SPLIT_ASPECT = 1.438
#: A screen whose long side is under this many times its short side is a
#: foldable's screen: both of the Duo's (1.42, 1.45), 4:3 (1.33) and anything
#: squarer. 3:2 (1.5, a Surface laptop) and every phone (1.78 and longer) are
#: not.
FOLD_MAX_ASPECT = 1.47
#: Whether to *offer* phone layout is another question, and shape cannot answer
#: it: every iPad is between 4:3 (1.33) and 1.52 : 1, the Duo's screens 1.42 and
#: 1.45, so an iPad reached over VNC at its native resolution is the Duo's
#: shape and a desk's size, and offering phone layout to every near-square
#: screen offered it to every iPad. So :func:`is_phone_shaped` takes a screen
#: for the Duo's only at the Duo's own resolution (:func:`is_duo_resolution`);
#: no iPad and no common monitor has a side of 1878, 2670, 1398 or 2034 px. A
#: Duo whose desktop RealVNC sets to another resolution is still offered phone
#: layout when that desktop is too small for the desk window, and the toggle
#: is there when it is not. The size tested is the *available* geometry, which
#: the Windows taskbar takes a strip from, so one side must match exactly and
#: the other may fall short by up to this many pixels: 48 px of taskbar at
#: 100 % scaling, 120 px at 250 %.
DUO_TASKBAR_SLACK_PX = 160

#: Touch sizes, in phone points. 44 pt is the smallest target a thumb hits
#: reliably (Apple's minimum; Material's 48 dp is the same physical size on a
#: 360-dp phone); bars, rows and menu items take 48. Targets sit at least 8 pt
#: apart. The one exception is a 96-well plate, which cannot give 12 wells
#: 44 pt each on a 390-pt screen; its wells are as large as the width allows.
TOUCH_MIN_PT = 44
TOUCH_ROW_PT = 48
TOUCH_GAP_PT = 8
TOP_BAR_PT = 52

#: Scroll bars, in phone points. Over VNC a scroll bar is grabbed with a thumb,
#: so its track is a touch target across (``SCROLLBAR_PT``), while the handle *drawn*
#: inside the track is slimmer (``SCROLL_HANDLE_PT``) so it does not read as a
#: control of its own. The handle is never shorter than a row
#: (``TOUCH_ROW_PT``), so it can be caught however long the page. Most
#: scrolling is a drag on the content itself (``ui/phone_shell.py``); the bar
#: is for the long jump.
SCROLLBAR_PT = 16

#: The Controls sheet lays a page's controls out as a form (one control per
#: row, ``widgets/control_band_form.py``); its label column is this wide, which
#: holds "X-axis · channel" at the Medium text size and wraps anything longer.
SHEET_LABEL_PT = 112
SCROLL_HANDLE_PT = 8

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

#: A screen at least this much taller than wide is a phone held upright (every
#: phone is 16:9, 1.78, or longer, and a taskbar's strip leaves 1.7 of that).
#: A squarer upright screen wide and tall enough for the desk window — an iPad
#: held upright, 1.33 to 1.52 — is a desk.
PHONE_UPRIGHT_MIN_ASPECT = 1.6

#: A screen this much wider than tall, and shorter than the second figure, is a
#: phone on its side rather than a monitor (see ``is_phone_shaped``).
PHONE_MIN_ASPECT = 1.95
PHONE_MAX_SHORT_SIDE = 1300

#: ``core.app_settings`` keys.
KEY_ON = "ui/phone_layout"
KEY_TEXT_SIZE = "ui/phone_text_size"

_state = {"on": False, "switching": 0}
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


def is_switching() -> bool:
    """True while the shell is re-laying the window out — entering or leaving
    phone layout, a text-size change, the phone turning.

    No timer and no queued call runs inside the restyle (its rescale pass
    delivers polish and layout events only), so this is for code reached
    synchronously from it — an ``_aw_rescale`` hook, a Show filter — that
    must not start work of its own while the pass walks the widgets.
    """
    return _state["switching"] > 0


@contextmanager
def switching() -> Iterator[None]:
    """Mark a switch in progress for :func:`is_switching`. Re-entrant."""
    _state["switching"] += 1
    try:
        yield
    finally:
        _state["switching"] -= 1


def add_listener(fn: Callable[[bool], None]) -> None:
    """Call ``fn(on)`` whenever phone layout turns on or off."""
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn: Callable[[bool], None]) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def follow(owner, fn: Callable[[bool], None]) -> None:
    """Call ``fn(on)`` whenever phone layout turns on or off, for as long as
    *owner* — a ``QObject``, usually a page's card — lives.

    The listener list is process-wide, so a listener that outlived its page would
    keep it alive and touch widgets that are gone. This holds *owner* only
    weakly, and takes the listener out the next time it is called after *owner*
    is collected or its C++ object is deleted (the Python wrapper can outlive
    it, and then any call on it raises ``RuntimeError``). *fn* is held strongly
    until then, so it may close over the owner's children but should not own the
    owner itself."""
    import weakref

    ref = weakref.ref(owner)

    def _listener(on: bool) -> None:
        live = ref()
        if live is not None:
            try:
                live.objectName()               # raises once the C++ object is gone
            except RuntimeError:
                live = None
        if live is None:
            remove_listener(_listener)
            return
        fn(on)

    add_listener(_listener)


def is_phone_shaped(width: int, height: int) -> bool:
    """Does a screen of this available size look like a phone?

    Too small in either direction to hold the desktop window; upright at a
    phone's proportions (``PHONE_UPRIGHT_MIN_ASPECT``); one of the iPhone
    Duo's screens at its own resolution (:func:`is_duo_resolution`); or a
    phone on its side. A tablet is none of these — an iPad either way up, at
    its native resolution, holds the desk window and is no phone's shape — and
    neither is a 4:3 monitor. A phone held sideways at its full physical
    resolution is indistinguishable from a wide monitor by size alone, so it
    reads as desk-shaped; the toggle is how the user says otherwise.
    """
    if width <= 0 or height <= 0:
        return False
    if width < DESK_MIN_W or height < DESK_MIN_H:
        return True
    if height > width and height / width >= PHONE_UPRIGHT_MIN_ASPECT:
        return True
    if is_duo_resolution(width, height):
        return True
    # Sideways at full resolution a phone is the size of a monitor, but not
    # its shape: phones are 19.5:9 or longer (2.17), monitors 16:9 (1.78) or
    # 16:10. Only an ultrawide 21:9 at 1080 lines tall reads as a phone too,
    # which costs it no more than an offer it can dismiss.
    return width / height >= PHONE_MIN_ASPECT and height < PHONE_MAX_SHORT_SIDE


def is_duo_resolution(width: int, height: int) -> bool:
    """Is a screen of this available size one of the iPhone Duo's screens at
    its own resolution, either way up — less at most a taskbar's strip off one
    side (``DUO_TASKBAR_SLACK_PX``, which says why not by shape)?"""
    short, long = sorted((width, height))
    slack = DUO_TASKBAR_SLACK_PX
    for n_short, n_long in (DUO_INNER_PX, DUO_OUTER_PX):
        if ((short == n_short and n_long - slack <= long <= n_long)
                or (long == n_long and n_short - slack <= short <= n_short)):
            return True
    return False


def keeps_phone_layout(width: int, height: int) -> bool:
    """Is a screen of this available size one phone layout, once on, stays on?

    Every phone-shaped screen, and every near-square one too
    (:func:`is_fold_shaped`): a Duo whose desktop RealVNC gives a resolution
    of its own is not offered phone layout by its shape, but once the user
    has turned it on there, a banner offering the desk back and a launch that
    reopens at the desk would undo their choice. An iPad on which the user
    turned phone layout on is held to it the same way."""
    return is_phone_shaped(width, height) or is_fold_shaped(width, height)


def duo_screen(width: int, height: int) -> str | None:
    """Which of a foldable's screens a screen of this size is, in either
    orientation: ``"inner"`` (unfolded), ``"outer"`` (folded), or ``None`` for
    any screen that is not near-square (``FOLD_MAX_ASPECT``).

    The Duo's own resolutions are matched exactly; any other near-square
    screen by its shape (``DUO_SPLIT_ASPECT``)."""
    short, long = sorted((width, height))
    if short <= 0:
        return None
    if (short, long) == DUO_INNER_PX:
        return "inner"
    if (short, long) == DUO_OUTER_PX:
        return "outer"
    aspect = long / short
    if aspect >= FOLD_MAX_ASPECT:
        return None
    return "inner" if aspect < DUO_SPLIT_ASPECT else "outer"


def is_fold_shaped(width: int, height: int) -> bool:
    """Is a screen of this size one of a foldable's near-square screens?"""
    return duo_screen(width, height) is not None


def is_unfolded(width: int, height: int) -> bool:
    """Is a screen of this size a foldable's inner screen, opened?"""
    return duo_screen(width, height) == "inner"


def reference_width_pt(width: int, height: int) -> int:
    """How many phone points the short side of a screen of this size is taken
    to be: the Duo's inner or outer screen's, otherwise a phone's 390."""
    return {"inner": DUO_INNER_PT, "outer": DUO_OUTER_PT}.get(
        duo_screen(width, height), PHONE_REFERENCE_WIDTH_PT)


def phone_unit(width: int, height: int) -> float:
    """Logical pixels per phone point on a screen of this size (at least 1)."""
    short = min(width, height)
    if short <= 0:
        return 1.0
    return max(1.0, short / reference_width_pt(width, height))


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
