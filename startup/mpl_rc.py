"""The matplotlib settings All-Well's figures are designed against, pinned over whatever the machine says.

matplotlib reads ``matplotlibrc`` files before the app runs a line — a site file (``/etc/matplotlib``), the
user's own (``~/.config/matplotlib``), or one named by ``MATPLOTLIBRC`` — and every figure the app draws
inherits them. Its figures are sized for matplotlib's own default of 100 dpi: a plot card's height is pinned in
pixels and its text is set in points, so a file saying ``figure.dpi: 200`` doubles every title, label and tick
against a card that stays the same size. That is what one machine's site file did: on every plot page titles
ran off the top of their figures and tick labels into one another.

:func:`pin` sets the values back. Call it once at start-up, before the first figure exists — ``allwell.main``
does, after ``startup.mpl_bootstrap.configure``, and so does the page-render harness
(``tests/_page_render_case.start_window``). Only what the figures' layout depends on is pinned; a user's choice
of anything else in their rc file is left alone. ``tests/test_figures_ignore_the_machines_matplotlibrc.py``
holds it.
"""

from __future__ import annotations

#: The rc values every All-Well figure is laid out against.
APP_RC = {
    # Points become pixels at this rate. A plot card pins its figure in pixels and sets its text in points, so
    # any other value rescales the text against the card.
    "figure.dpi": 100.0,
}


def pin() -> None:
    """Apply :data:`APP_RC` over whatever the machine's rc files set. Safe to call more than once."""
    import matplotlib

    matplotlib.rcParams.update(APP_RC)
