"""Home's plate paints in the app's status vocabulary, one colour per state.

Three properties, each of which was violated by a plate that invented its own
colours: no two states may look alike, every painted colour must read against
the panel it sits on (a well the same tone as its background is a well that
cannot be found), and the colours must be the tokens the other plate maps in the
app already use for the same jobs — Analyze's run plate first, because it is the
same kind of map.

The measurements are on the tokens rather than on rendered pixels so they run in
the fast lane. ``tests/test_home_plate_render.py`` closes that gap by sampling
what the widget actually paints, and
``tests/test_home_plate_is_colour_blind_safe.py`` measures every pair under
simulated colour-vision deficiency.
"""
from __future__ import annotations

import pytest

from ui import theme
from review.features.home import collect as hc
from review.features.home.plate_panel import (
    NEUTRAL_STATES,
    STATE_LABELS,
    STATE_TOKENS,
    state_colors,
)
from review.features.home.tab_view import legend_swatch_style

#: The tokens Home's plate may paint with, and where each one comes from:
#: Analyze's run plate (``run_done``, ``run_pending``, ``run_problem``) for the
#: facts the two plates share, the shared status tokens ``accent`` and
#: ``stale`` for the anomalies Home shares with Concatenate and Validate, and
#: ``home_partial`` for the one state no other plate shows. ``accent2`` is
#: excluded on purpose: it means "the other of two normal groups", which no
#: state on this page is. Not ``success`` / ``warn`` / ``danger``: green, amber
#: and red are what red/green colour-vision deficiency could not tell apart.
STATUS_TOKENS = ("run_done", "run_pending", "run_problem", "accent", "stale", "home_partial")


def _rgb(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _relative_luminance(value: str) -> float:
    def channel(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _channel_distance(a: str, b: str) -> int:
    return sum(abs(x - y) for x, y in zip(_rgb(a), _rgb(b)))


def test_no_two_states_are_painted_the_same_colour():
    """Two states in one colour make the legend unusable: the reader matches a
    well to a row and gets two answers."""
    by_colour: dict[str, list[str]] = {}
    for state, colour in state_colors().items():
        by_colour.setdefault(colour.upper(), []).append(state)
    clashes = {c: s for c, s in by_colour.items() if len(s) > 1}
    assert not clashes, f"states sharing a colour: {clashes}"


def test_no_two_states_are_painted_in_indistinguishable_colours():
    """Distinct hex values are not enough. ``warn`` (#F59E0B) and ``threshold``
    (#F5A524) are different tokens with the same hue, the same value and 32
    total channel steps between them — on a 17-pixel well they are one colour.
    """
    colours = state_colors()
    states = sorted(colours)
    too_close = [
        (a, b, colours[a], colours[b], _channel_distance(colours[a], colours[b]))
        for i, a in enumerate(states) for b in states[i + 1:]
        if _channel_distance(colours[a], colours[b]) < 90
    ]
    assert not too_close, f"states too close to tell apart: {too_close}"


def test_every_painted_colour_reads_against_the_panel():
    """A well painted in a near-background tone is a well the reader cannot see.
    3.0 is the WCAG floor for a non-text graphical object."""
    panel = theme.Colors.panel
    unreadable = {
        state: (colour, round(_contrast(colour, panel), 2))
        for state, colour in state_colors().items()
        if _contrast(colour, panel) < 3.0
    }
    assert not unreadable, f"states that vanish into the panel: {unreadable}"


def test_painted_colours_come_from_the_status_vocabulary():
    """The point of the rule: a colour learnt on one plate map means the same
    thing on this one."""
    stray = {s: t for s, t in STATE_TOKENS.items() if t not in STATUS_TOKENS}
    assert not stray, f"states painted outside the status vocabulary: {stray}"


@pytest.mark.parametrize("state,token,job", [
    (hc.CELL_COMPLETE, "run_done", "Analyze's downloaded / thresholded well"),
    (hc.CELL_PARTIAL, "home_partial", "part of the well covered — Home's own amber"),
    (hc.CELL_FAILED, "run_problem", "Analyze's problem fill"),
    (hc.CELL_NO_MEASUREMENTS, "accent",
     "Concatenate's merged-with-no-measurements-CSV anomaly"),
    (hc.CELL_CSV_ONLY, "stale", "Validate's output-present, archive-missing well"),
    (hc.CELL_INPUT_ONLY, "run_pending",
     "Analyze's grouped, queued for processing"),
])
def test_each_state_carries_the_token_that_job_carries_elsewhere(state, token, job):
    assert state_colors()[state] == getattr(theme.Colors, token), f"{state} should read as: {job}"


def test_the_states_that_assert_nothing_carry_no_colour():
    """``unchecked`` and ``no_output`` describe an absence. Painting them at all
    would claim something; they get the plate's own uncoloured cell."""
    for state in NEUTRAL_STATES:
        assert state not in STATE_TOKENS
    assert set(NEUTRAL_STATES) == {hc.CELL_UNCHECKED, hc.CELL_NO_OUTPUT}


def test_every_state_the_plate_can_paint_has_a_label():
    """Including the neutral ones — the tooltip is the only thing that separates
    them, so it cannot be missing."""
    painted = set(STATE_TOKENS) | set(NEUTRAL_STATES)
    assert painted == set(STATE_LABELS)


def test_the_legend_swatch_shows_what_the_plate_paints():
    """A filled chip beside a state the plate leaves empty is a legend the
    reader cannot match to the plate."""
    for state, colour in state_colors().items():
        assert colour in legend_swatch_style(state)
    for state in NEUTRAL_STATES:
        style = legend_swatch_style(state)
        assert "transparent" in style and "border" in style
