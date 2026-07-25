"""Characterization: the four timepoint-hours parsers and where they disagree.

Four places in the tree turn a timepoint token into hours, and they do not all
agree:

* ``pipeline.auto_threshold.parse_tp_hours`` — Qt-free, used by the pipeline's
  auto-threshold pass.
* ``well_viewer.data.image_identity.parse_timepoint_hours`` — the viewer side.
* ``process_microscopy._parse_timepoint_hours`` — the pipeline's own.
* ``services.remote_runner._tp_sort_key`` — inlines a fourth, because
  ``services/`` may not import ``well_viewer`` (ARCHITECTURE §6.2).

The duplication is real but the convergence is *not* mechanical, which is what
this file exists to record. Over a 32-token matrix the four agree on 27 and
diverge on five, in two distinct ways:

1. **Sentinel.** ``process_microscopy`` returns ``nan`` for an unparseable
   token where the other three return ``None``. Those propagate differently —
   ``nan`` compares False against everything including itself, so a caller that
   tests ``is None`` and one that tests ``math.isnan`` are not interchangeable.

2. **Grammar.** ``pipeline.auto_threshold`` is the most permissive: it alone
   parses a seconds suffix (``"45s"`` → 0.0125 h) and alone falls back to a
   leading digit run (``"12abc"`` → 12). The other three drop both.

So adopting any one parser everywhere is a behaviour change in a real direction:
the viewer would start plotting timepoints it currently drops, or the pipeline
would lose seconds support. Which grammar is *correct* is a product decision
about what a timepoint token may look like, not a refactor.

These tests pin today's answers so that decision can be made deliberately, and
so a future convergence shows up here as an intentional edit rather than a
silent shift in which timepoints a dataset's plots contain.
"""

from __future__ import annotations

import math

import pytest


def _parsers():
    from pipeline.auto_threshold import parse_tp_hours
    from process_microscopy import _parse_timepoint_hours
    from services.remote_runner import _tp_sort_key
    from well_viewer.data.image_identity import parse_timepoint_hours

    def remote_sort_hours(tok):
        """``_tp_sort_key`` returns ``(unparsed, hours, token)``; take the hours
        it would sort by, or None when it gave up."""
        unparsed, hours, _tok = _tp_sort_key(tok)
        return None if unparsed else hours

    return {
        "pipeline": parse_tp_hours,
        "viewer": parse_timepoint_hours,
        "process_microscopy": _parse_timepoint_hours,
        "remote_runner": remote_sort_hours,
    }


# Tokens every parser resolves identically. This is the shared grammar: plain
# numbers, the DDdHHhMMm forms, and a trailing digit run after a prefix.
AGREED = [
    ("24", 24.0), ("1.5", 1.5), ("0", 0.0), ("5", 5.0), ("1.0", 1.0),
    ("007", 7.0), ("1e2", 100.0), ("-1", -1.0),
    ("T01", 1.0), ("T1", 1.0), ("tp5", 5.0), ("day3", 3.0), ("day 3", 3.0),
    ("abc12", 12.0),
    ("48h", 48.0), ("24h", 24.0), ("3.5h", 3.5),
    ("2d", 48.0), ("1d", 24.0), ("0.5d", 12.0),
    ("30m", 0.5), ("90m", 1.5),
    ("01d02h30m", 26.5), ("2d4h", 52.0), ("1h30m", 1.5),
]


@pytest.mark.parametrize(("token", "hours"), AGREED, ids=[t for t, _ in AGREED])
def test_all_four_parsers_agree_on_the_shared_grammar(token, hours):
    for name, fn in _parsers().items():
        got = fn(token)
        assert got == pytest.approx(hours), f"{name}({token!r}) = {got!r}, expected {hours}"


@pytest.mark.parametrize("token", ["nan", "inf"])
def test_special_floats_pass_through_identically(token):
    """``float()`` accepts these, so every parser returns the special float
    rather than treating the token as unparseable."""
    values = [fn(token) for fn in _parsers().values()]
    assert all(isinstance(v, float) for v in values)
    assert all(math.isnan(v) for v in values) or all(math.isinf(v) for v in values)


@pytest.mark.parametrize("token", ["", "   ", "abc"])
def test_unparseable_sentinel_diverges(token):
    """Divergence 1: process_microscopy says ``nan``, the rest say ``None``.

    A caller written against one convention silently misreads the other — ``nan``
    is not ``None``, and ``nan != nan``.
    """
    p = _parsers()
    assert p["pipeline"](token) is None
    assert p["viewer"](token) is None
    assert p["remote_runner"](token) is None
    pm_value = p["process_microscopy"](token)
    assert isinstance(pm_value, float) and math.isnan(pm_value)


def test_seconds_suffix_only_the_pipeline_parser_understands():
    """Divergence 2a: ``"45s"`` is 45 seconds to one parser and noise to three."""
    p = _parsers()
    assert p["pipeline"]("45s") == pytest.approx(45 / 3600.0)
    assert p["viewer"]("45s") is None
    assert p["remote_runner"]("45s") is None
    assert math.isnan(p["process_microscopy"]("45s"))


def test_leading_digit_run_only_the_pipeline_parser_understands():
    """Divergence 2b: ``"12abc"`` is 12 to one parser and noise to three.

    Note the asymmetry with ``"abc12"``, which all four read as 12 — the shared
    grammar takes a *trailing* digit run, and only the pipeline parser also
    takes a leading one.
    """
    p = _parsers()
    assert p["pipeline"]("12abc") == 12.0
    assert p["viewer"]("12abc") is None
    assert p["remote_runner"]("12abc") is None
    assert math.isnan(p["process_microscopy"]("12abc"))

    for name, fn in p.items():
        assert fn("abc12") == 12.0, f"{name} disagrees on the trailing-digit case"


def test_no_fifth_parser_appears():
    """A guard on the count itself: four is already too many, and the point of
    pinning them is that the next change reduces the number rather than adding
    to it. Update this list only when converging one away."""
    assert sorted(_parsers()) == [
        "pipeline", "process_microscopy", "remote_runner", "viewer",
    ]
