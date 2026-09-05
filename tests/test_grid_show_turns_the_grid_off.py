"""Properties → Grid → **Show grid**, off, actually removes the grid.

It did not, on any plot in the app. ``apply_export_style_prefs`` called

    ax.grid(show_grid, alpha=…, linestyle=…)

and matplotlib reads line properties beside a false first argument as "style the
grid", turns it back **on**, and says so:

    UserWarning: First parameter to grid() is false, but line properties are
    supplied. The grid will be enabled.

So the toggle moved, the pref was stored, the figure redrew — and the gridlines
stayed exactly where they were, in both directions. Nothing was reading the
warning, and no test looked at the rendered figure, which is the only place the
difference was ever going to show.

That is what these assertions are on: the figure rendered to PNG and hashed.
Asserting ``ax.xaxis.get_gridlines()[0].get_visible()`` would not have caught it
— a gridline's own visibility flag is not what ``ax.grid(False)`` clears, so
that assertion passes against the broken code, which is the first thing this
file was written to check.
"""

from __future__ import annotations

import hashlib
import io

from matplotlib.figure import Figure

from well_viewer.core.plot_style import (
    DEFAULT_EXPORT_STYLE_PREFS,
    apply_export_style_prefs,
)


def _rendered(fig) -> str:
    """What the user sees, hashed."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    FigureCanvasAgg(fig)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=72)
    return hashlib.sha256(buf.getvalue()).hexdigest()


def _styled(**overrides) -> str:
    """A small line plot styled with *overrides*, rendered and hashed."""
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot(1, 1, 1)
    ax.plot([1, 2, 3], [1, 4, 9])
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs.update(overrides)
    apply_export_style_prefs(fig, prefs)
    return _rendered(fig)


def test_the_grid_off_does_not_render_the_same_as_the_grid_on():
    assert _styled(grid_show=True) != _styled(grid_show=False)


def test_the_grid_off_renders_the_same_whatever_its_styling_says():
    """Off is off, not "off but dotted".

    The bug was born of passing the styling kwargs alongside a false flag, so
    the case that pins the fix is a figure whose grid is off while the opacity
    and line style say something else entirely. Both must render identically to
    each other — and, by the test above, differently from a grid that is on.
    """
    plain = _styled(grid_show=False)
    styled = _styled(grid_show=False, grid_alpha=0.95, grid_style=":")
    assert plain == styled


def test_the_styling_still_governs_a_grid_that_is_on():
    """The other half: the fix must not have taken the styling away with it."""
    assert (_styled(grid_show=True, grid_alpha=0.2, grid_style="--")
            != _styled(grid_show=True, grid_alpha=0.95, grid_style=":"))


def test_turning_the_grid_off_warns_about_nothing():
    """The warning matplotlib emitted is the evidence the call was wrong.

    Kept as an assertion rather than a comment: an edit that reintroduces the
    styling kwargs beside a false flag restores both the warning and the
    defect, and this is the cheapest thing that notices.
    """
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _styled(grid_show=False)
    offenders = [str(w.message) for w in caught
                 if "line properties are supplied" in str(w.message)]
    assert not offenders, offenders
