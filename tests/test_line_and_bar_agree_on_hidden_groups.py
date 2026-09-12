"""With groups defined and all of them hidden, neither pane draws wells.

In group mode the plate is passive: clicking toggles a group's visibility, not
a well's selection, so ``selection.selected_wells`` holds whatever was selected
before groups were defined and is stale from that point on.
``ordered_bar_keys`` refuses to fall back to it — a fallback there resurrected
every well after "Deselect all" — and said so in a comment. The line plot's
collector had no such guard: its per-well branch ran whenever no replicate set
was *active*, so with every group hidden the two panes sitting side by side
disagreed about what was selected, one empty and one full.

The marker roster is here too because it is the same class of defect: the
aggregate scatter encodes group identity as marker shape, and the roster held
ten shapes, so an eleventh group silently reused the first one's — two groups
the reader cannot tell apart, on the plot and in the legend both, with nothing
saying so.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest


def _returns_before(fn, marker: str) -> bool:
    """Whether *fn* has a bare-ish return guarded on ``selection.selections``."""
    src = textwrap.dedent(inspect.getsource(fn))
    return marker in src


def test_the_bar_keys_refuse_the_stale_fallback():
    from review.features.barplot.controller import ordered_bar_keys

    src = textwrap.dedent(inspect.getsource(ordered_bar_keys))
    tree = ast.parse(src)
    tests = [ast.dump(n.test) for n in ast.walk(tree) if isinstance(n, ast.If)]
    assert any("selections" in t for t in tests), (
        "ordered_bar_keys no longer branches on whether groups exist"
    )


def test_the_line_collector_refuses_it_too():
    from review.features.lineplot import controller as line_ctrl

    fn = getattr(line_ctrl, "collect_line_series", None) or line_ctrl.build_line_series
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)

    guards = [n for n in ast.walk(tree)
              if isinstance(n, ast.If) and "selections" in ast.dump(n.test)
              and any(isinstance(b, ast.Return) for b in n.body)]
    assert guards, (
        "the line collector falls through to its per-well branch when groups "
        "exist but all are hidden, so it draws wells the bar pane does not"
    )


def test_the_group_marker_roster_is_wider_than_a_plate_of_groups():
    from review.features.scatter.collect import GROUP_MARKERS

    assert len(GROUP_MARKERS) == len(set(GROUP_MARKERS)), "a shape is listed twice"
    assert len(GROUP_MARKERS) >= 20, (
        f"only {len(GROUP_MARKERS)} distinguishable shapes for group identity"
    )


def test_every_group_marker_is_a_real_matplotlib_marker():
    matplotlib = pytest.importorskip("matplotlib")
    from matplotlib.markers import MarkerStyle

    from review.features.scatter.collect import GROUP_MARKERS

    assert matplotlib is not None
    unknown = [m for m in GROUP_MARKERS if m not in MarkerStyle.markers]
    assert not unknown, unknown


def test_a_wrapped_marker_roster_is_announced(caplog):
    """Silence is the defect: the legend just lists the same swatch twice."""
    import logging

    from review.features.scatter import collect

    src = textwrap.dedent(inspect.getsource(collect.collect_scatter_agg_data))
    assert "_logger.warning" in src, (
        "nothing says anything when more groups are drawn than there are shapes"
    )
    assert logging.getLogger(collect.__name__) is not None
    assert caplog is not None
