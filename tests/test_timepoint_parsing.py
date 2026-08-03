"""One timepoint grammar, four names, and the guarantee that they agree.

Reading a timepoint token as hours used to have four implementations. Three
shared a grammar and differed only in sentinel; the fourth
(``pipeline.auto_threshold.parse_tp_hours``) scavenged the first digit run from
anywhere in the token, which made it read ``"5%"`` as five hours and disagree
about ``"a1b2"`` (1 versus 2) and ``"12.5.3"`` (12 versus 5.3). It fed only a
sort, so nothing visibly broke — but a second answer to "what hour is this
token" is a latent key mismatch waiting for someone to reuse it.

They now all delegate to :mod:`util.timepoint`, and these tests are what keeps
that true. The names survive because call sites are written against them:

* ``util.timepoint.parse_hours`` — the implementation.
* ``well_viewer.data.image_identity.parse_timepoint_hours`` — the viewer's name,
  consumed by ``normalize_tp`` (canonical keys) and ``tp_hours``.
* ``pipeline.auto_threshold.parse_tp_hours`` — the pipeline's name.
* ``process_microscopy._parse_timepoint_hours`` — a ``nan``-returning adapter;
  see below.

``util.timepoint.sort_key`` likewise replaced six hand-written
``(unparseable, hours, token)`` tuples, which is how two tabs could order the
same timepoints differently.
"""

from __future__ import annotations

import math
import random
import string
import textwrap

import pytest


def _parsers():
    """The ``None``-returning parsers. ``process_microscopy`` is excluded — it
    is an adapter with a different sentinel and gets its own tests."""
    from pipeline.auto_threshold import parse_tp_hours
    from util.timepoint import parse_hours
    from well_viewer.data.image_identity import parse_timepoint_hours

    return {
        "util.timepoint": parse_hours,
        "viewer": parse_timepoint_hours,
        "pipeline": parse_tp_hours,
    }


# One representative token per grammar branch, so a rewrite that drops a branch
# fails here rather than in a plot.
GRAMMAR = [
    ("24", 24.0), ("1.5", 1.5), ("0", 0.0), ("1e2", 100.0), ("-1", -1.0),
    ("007", 7.0),
    ("48h", 48.0), ("3.5h", 3.5), ("3hours", 3.0),
    ("2d", 48.0), ("0.5d", 12.0), ("2days", 48.0),
    ("30m", 0.5), ("90m", 1.5), ("90min", 1.5),
    ("01d02h30m", 26.5), ("2d4h", 52.0), ("1h30m", 1.5), ("1d", 24.0),
    ("T01", 1.0), ("T1", 1.0), ("tp5", 5.0), ("day3", 3.0), ("day 3", 3.0),
    ("abc12", 12.0),
]

# Tokens with no readable hour. The parser must say so rather than guess — a
# guessed number becomes a canonical key nothing else in the app recognises.
OPAQUE = ["", "   ", "abc", "treated_arm", "45s", "12abc", "5%", "__7__",
          "1iEl", "2hpCh"]


@pytest.mark.parametrize(("token", "hours"), GRAMMAR, ids=[t for t, _ in GRAMMAR])
def test_every_parser_reads_the_shared_grammar(token, hours):
    for name, fn in _parsers().items():
        got = fn(token)
        assert got == pytest.approx(hours), f"{name}({token!r}) = {got!r}, want {hours}"


@pytest.mark.parametrize("token", OPAQUE, ids=repr)
def test_every_parser_rejects_the_same_opaque_tokens(token):
    for name, fn in _parsers().items():
        assert fn(token) is None, f"{name}({token!r}) = {fn(token)!r}, want None"


@pytest.mark.parametrize("token", ["nan", "inf", "-inf"])
def test_special_floats_parse_rather_than_reject(token):
    """``float()`` accepts these, so they are parsed values, not failures.
    A caller that needs a real measurement checks ``math.isfinite``."""
    for name, fn in _parsers().items():
        value = fn(token)
        assert isinstance(value, float), f"{name}({token!r}) rejected it"
        assert not math.isfinite(value)


def test_parsers_agree_under_fuzz():
    """The guarantee, stated directly: every parser is the same function.

    A fuzz rather than a fixed list, because the failure mode being guarded is
    someone reintroducing a local implementation that looks right on the obvious
    inputs and drifts on the ragged ones — which is exactly what happened.
    """
    fns = list(_parsers().values())
    random.seed(11)
    alphabet = string.ascii_letters + string.digits + " .:-_dhms%"
    tokens = [t for t, _ in GRAMMAR] + OPAQUE + [
        "".join(random.choice(alphabet) for _ in range(random.randint(1, 8)))
        for _ in range(2000)
    ]

    def same(a, b):
        if a is None or b is None:
            return a is b
        return a == b or (math.isnan(a) and math.isnan(b))

    mismatches = [
        (tok, [fn(tok) for fn in fns])
        for tok in tokens
        if not all(same(fns[0](tok), fn(tok)) for fn in fns[1:])
    ]
    assert not mismatches, f"parsers diverged on {len(mismatches)} token(s): {mismatches[:5]}"


class TestProcessMicroscopyAdapter:
    """``process_microscopy`` keeps a ``nan`` sentinel where the rest return
    ``None``, so ``timepoint_hours`` stays a float column with nothing to
    special-case on the way into the CSV. It must otherwise be the same
    function."""

    @staticmethod
    def _pm():
        import process_microscopy as pm
        return pm._parse_timepoint_hours

    @pytest.mark.parametrize(("token", "hours"), GRAMMAR, ids=[t for t, _ in GRAMMAR])
    def test_reads_the_same_grammar(self, token, hours):
        assert self._pm()(token) == pytest.approx(hours)

    @pytest.mark.parametrize("token", OPAQUE, ids=repr)
    def test_rejects_the_same_tokens_as_nan(self, token):
        value = self._pm()(token)
        assert isinstance(value, float) and math.isnan(value)

    def test_nan_sentinel_cannot_be_told_from_a_parsed_nan(self):
        """A documented consequence, pinned so it is a known trade rather than a
        surprise: the literal token ``"nan"`` and an unreadable token both come
        back as ``nan``. Both current callers treat them identically. A caller
        that must distinguish them should use ``util.timepoint.parse_hours``.
        """
        pm = self._pm()
        assert math.isnan(pm("nan")) and math.isnan(pm("not-a-time"))


class TestSortKey:
    def test_orders_chronologically_not_lexically(self):
        from util.timepoint import sort_key
        tokens = ["T10", "T2", "T1", "48h", "2d", "30m"]
        # "48h" and "2d" are both 48.0, so they tie on hours and the token
        # itself breaks it — which is what makes the ordering deterministic.
        assert sorted(tokens, key=sort_key) == ["30m", "T1", "T2", "T10", "2d", "48h"]

    def test_opaque_tokens_sort_last_as_a_stable_block(self):
        from util.timepoint import sort_key
        tokens = ["zzz", "24", "abc", "1"]
        assert sorted(tokens, key=sort_key) == ["1", "24", "abc", "zzz"]

    def test_every_timepoint_sort_uses_the_shared_key(self):
        """Six call sites wrote the ``(unparseable, hours, token)`` tuple by hand,
        which is how the tabs drifted out of agreement. This asserts the names
        that remain all resolve to the one implementation."""
        from util.timepoint import sort_key
        import pipeline.auto_threshold as at
        import services.remote_runner as rr
        from well_viewer.features.image_browser.image_table import grid_layout
        from well_viewer.features.image_browser.review_image import collect

        assert at.tp_sort_key is sort_key
        assert grid_layout._tp_sort_key is sort_key
        assert collect._review_tp_sort_key is sort_key
        # remote_runner keeps a wrapper (its name is used as a bare callable in
        # a sorted() call); prove it delegates rather than reimplements.
        for token in ("2hpCh", "T01", "abc", "48h"):
            assert rr._tp_sort_key(token) == sort_key(token)


class TestCanonicalKeyForm:
    """``normalize_tp`` — the one key form every identity comparison uses."""

    def test_zero_is_a_timepoint_not_an_absence(self):
        """Regression: the baseline timepoint used to normalise to ``""``.

        The guard tested the *value's* truthiness (``str(value or "")``), and a
        numeric ``0`` is falsy — so the same t=0 produced ``"0"`` when it arrived
        as text and ``""`` when it arrived as a float. The empty key matches
        nothing, so a lookup keyed on a float 0.0 silently found no images and no
        rows. Emptiness is a property of the string, not of the number.
        """
        from well_viewer.data.image_identity import normalize_tp
        assert normalize_tp(0.0) == "0"
        assert normalize_tp(0) == "0"
        assert normalize_tp("0") == "0"
        assert normalize_tp("0.0") == "0"
        assert normalize_tp("T00") == "0"

    def test_genuinely_absent_values_still_normalise_to_empty(self):
        from well_viewer.data.image_identity import normalize_tp
        assert normalize_tp(None) == ""
        assert normalize_tp("") == ""
        assert normalize_tp("   ") == ""

    def test_numeric_and_text_spellings_agree_for_every_timepoint(self):
        """The property the zero case violated: one timepoint, one key."""
        from well_viewer.data.image_identity import normalize_tp
        for hours in (0.0, 0.5, 1.0, 4.0, 23.5, 48.0):
            assert normalize_tp(hours) == normalize_tp(str(hours))

    def test_the_ctx_leaf_agrees_with_the_canonical_normaliser(self):
        """``core.ui_helpers.norm_timepoint`` is the ctx-shaped wrapper. It
        carried its own copy of the rule, which is how it kept the zero bug
        after ``normalize_tp`` was fixed."""
        from well_viewer.core.ui_helpers import norm_timepoint
        from well_viewer.data.image_identity import normalize_tp
        for value in (0.0, 0, "0", "T01", "1h30m", "treated_arm_2", "", None):
            assert norm_timepoint(None, value) == normalize_tp(value)

    def test_the_ctx_leaf_delegates_rather_than_reimplements(self):
        """Structure, not behaviour — and the two are not the same guard.

        The agreement test above passes against *any* correct copy of the rule,
        including an inlined one, so it cannot see the thing that caused the bug:
        a second implementation that is right today and drifts tomorrow. This
        reads the function's own body and requires it to be a single delegating
        return, which is what the docstring on the shipped function claims.
        """
        import ast
        import inspect
        from well_viewer.core import ui_helpers

        src = inspect.getsource(ui_helpers.norm_timepoint)
        fn = ast.parse(textwrap.dedent(src)).body[0]
        body = [n for n in fn.body if not (isinstance(n, ast.Expr)
                                           and isinstance(n.value, ast.Constant))]
        assert len(body) == 1 and isinstance(body[0], ast.Return), (
            "norm_timepoint should be one delegating return; it has grown a body "
            "again, which is how the zero bug survived in it once already"
        )
        called = {n.func.id for n in ast.walk(body[0])
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "normalize_tp" in called, (
            "norm_timepoint no longer calls image_identity.normalize_tp — the "
            "timepoint rule has a second implementation again"
        )
