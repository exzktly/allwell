"""The Bar plot under a time-rescale factor.

The bar x-axis is categorical: the timepoint is *text* on the tick, not a
position, so there is no formatter to intercept and the rescale has to reach the
label builders themselves.

The line that matters here is between a bar's **label** and its **key**. The key
is identity — drag order, the x-position map, and the significance-bracket
matching all resolve through it — so it must not move when the user changes how
the axis is printed. Only the text does.
"""

from __future__ import annotations

import pytest

from well_viewer.features.barplot.controller import collect_bar_items


@pytest.fixture
def rescaled(mock_app):
    """The mock app with a ×4 label multiplier in force."""
    mock_app._time_scale = 4
    return mock_app


def _by_key(items):
    return {it.key: it for it in items}


# ── labels move ─────────────────────────────────────────────────────────────

def test_timepoint_labels_are_multiplied(rescaled):
    _, items, _ = collect_bar_items(rescaled._ctx, [1.0, 2.0])
    labels = {it.display for it in items}
    assert any("4" in lbl for lbl in labels)
    assert any("8" in lbl for lbl in labels)


def test_no_unscaled_timepoint_text_survives_anywhere(rescaled):
    """Every string a bar can print carries the same factor.

    A bar's timepoint reaches the figure as either the tick text (``display``)
    or the cluster header (``group_key``), depending on ``bar_group_mode``.
    Scaling one and not the other would put 4 and 1 on the same plot for the
    same timepoint, which is worse than not scaling at all.
    """
    _, items, _ = collect_bar_items(rescaled._ctx, [1.0, 2.0])
    printed = {it.display for it in items} | {it.group_key for it in items if it.group_key}
    assert printed, "nothing drawn — the assertion below would be vacuous"
    assert not (printed & {"t=1", "t=2", "1", "2"}), printed
    assert printed & {"t=4", "t=8"}, printed


def test_labels_are_untouched_when_the_factor_is_off(mock_app):
    _, items, _ = collect_bar_items(mock_app._ctx, [1.0, 2.0])
    labels = {it.display for it in items}
    assert any("1" in lbl for lbl in labels)
    assert not any("4" in lbl for lbl in labels)


# ── keys do not ─────────────────────────────────────────────────────────────

def test_bar_keys_are_identical_with_and_without_the_factor(mock_app):
    """The key is the bar's identity.

    ``ctx.runtime.barplot.xs_keys`` maps it to an x position, the drag-reorder
    list stores it, and ``draw_significance_brackets`` matches a comparison to a
    bar through it. A label-only feature that moved it would silently break
    every one of those.
    """
    _, plain, _ = collect_bar_items(mock_app._ctx, [1.0, 2.0])
    plain_keys = [it.key for it in plain]

    mock_app._time_scale = 4
    _, scaled, _ = collect_bar_items(mock_app._ctx, [1.0, 2.0])

    assert [it.key for it in scaled] == plain_keys


def test_the_underlying_timepoint_is_unscaled(rescaled):
    """``BarItem.tp`` is the value the data was aggregated at, and the export
    writer emits it. It stays in the hours the pipeline parsed."""
    _, items, _ = collect_bar_items(rescaled._ctx, [1.0, 2.0])
    assert sorted({it.tp for it in items}) == [1.0, 2.0]


def test_bar_values_are_unchanged_by_the_factor(mock_app):
    """A label pass must not move a single number on the y-axis."""
    _, plain, _ = collect_bar_items(mock_app._ctx, 1.0)
    plain_means = {it.key: it.mean for it in plain}

    mock_app._time_scale = 4
    _, scaled, _ = collect_bar_items(mock_app._ctx, 1.0)

    assert {it.key: it.mean for it in scaled} == plain_means
