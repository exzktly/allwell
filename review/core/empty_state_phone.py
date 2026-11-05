"""What an empty plot's hint says in phone layout.

At the desk the plate is in the rail at the window's left; in phone layout it is
in the Wells sheet, opened from the strip at the foot of the screen, and Saved
selections stay in ☰ — so a hint written for the desk sends a phone user looking
for a plate that is not there, and "Click" is not what a finger does.

This is Review's one place that rewords a hint for the phone. The hints
themselves (``review.core.ui_helpers``) are written for the desk and know
nothing of it.
"""

from __future__ import annotations

from core import phone_layout

#: Desk wording -> phone wording, applied in order.
_PHONE_WORDING = (
    (" on the plate at the left", " on the plate (Wells, at the foot of the screen)"),
    ("Saved selections below it", "Saved selections in ☰"),
    ("Click ", "Tap "),
)


def phone_wording(hint: str) -> str:
    """*hint* as it reads in phone layout: the plate is under Wells, not at
    the left, and a finger taps. Unchanged at the desk."""
    if not phone_layout.is_on() or not hint:
        return hint
    for desk, phone in _PHONE_WORDING:
        hint = hint.replace(desk, phone)
    return hint


def follow_phone_layout(win) -> None:
    """Re-word every empty plot's hint (:func:`phone_wording`) when phone
    layout turns on or off, rather than at the next refresh — which, for a
    page already showing its empty state, might be the next dataset. Holds
    *win* weakly and drops its listener once *win* is gone."""
    import weakref

    from core.safe import swallow
    from review.core.ui_helpers import refresh_plot_empty_states
    ref = weakref.ref(win)

    def _on_change(_on: bool) -> None:
        w = ref()
        if w is None:
            phone_layout.remove_listener(_on_change)
            return
        with swallow("re-wording the empty plots for phone layout"):
            refresh_plot_empty_states(w)

    phone_layout.add_listener(_on_change)
