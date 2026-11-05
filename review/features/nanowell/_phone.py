"""Where the Nanowells page reads whether phone layout is on.

The page lays out differently on a phone: the site detail takes the whole page
while a nanowell is selected, the crop strip wraps to its own shape, and the
distribution figure's footer wraps to the figure's width. Those decisions are
made here and nowhere else in the feature, so the mode is read in one place
(``tests/test_phone_mode_stays_in_views.py``). The renderer in particular never
reads it: ``renderer.render_distribution`` is told ``wrap_footer`` by its caller,
the controller, which asks :func:`on_phone`.
"""

from __future__ import annotations

from collections.abc import Callable

from core import phone_layout


def on_phone() -> bool:
    """Whether phone layout is on."""
    return phone_layout.is_on()


def follow(listener: Callable[[bool], None]) -> None:
    """Call ``listener(on)`` whenever phone layout turns on or off."""
    phone_layout.add_listener(listener)


def stop_following(listener: Callable[[bool], None]) -> None:
    phone_layout.remove_listener(listener)
