"""A QC box reading ``0`` is *no bound*, and a max bound of 0 excludes everything.

``qc_params._pos`` states the rule and the reason: "A 0 / negative / blank value
means 'no bound' … avoiding the ``max_area == 0`` foot-gun of excluding every
cell." A minimum of zero keeps the same population no bound keeps, so nothing is
lost either way; a *maximum* of zero keeps nothing at all.

This file exists because a change went the other way on the strength of a
falsy-zero argument that is right in general and wrong here: it coerced the box
text itself, handed the result to the ``QCParams`` constructor, and closed with
a bare ``.with_updates()``. That call normalises nothing — ``with_updates``
filters on ``k in changes``, so with no keyword arguments the loop never runs —
so a typed ``0`` reached ``max_area`` intact and every plot went blank. The Max
circularity box applies live while typing, so pausing after the leading ``0`` of
"0.9" was enough.

The bounds are therefore built by passing the raw text *as keyword arguments*,
which is the only spelling that runs the normalisation.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pandas")

import pandas as pd  # noqa: E402

from review.domain.qc_params import QCParams, compute_qc_pass  # noqa: E402

_CELLS = pd.DataFrame({
    "area_px": [100.0, 200.0, 300.0],
    "shape_solidity": [0.9, 0.9, 0.9],
    "shape_circularity": [0.8, 0.8, 0.8],
    "qc_region_area": [500.0, 500.0, 500.0],
})

_MAXIMA = ["max_area", "max_region_area", "max_circularity"]
_MINIMA = ["min_area", "min_solidity", "min_circularity"]


@pytest.mark.parametrize("field", _MAXIMA + _MINIMA)
@pytest.mark.parametrize("text", ["0", "0.0", "", "   ", "-1", "not a number"])
def test_a_blank_zero_or_junk_box_is_no_bound(field, text):
    params = QCParams(enabled=True).with_updates(**{field: text})
    assert getattr(params, field) is None, (
        f"{field}={text!r} became a live bound"
    )


@pytest.mark.parametrize("field", _MAXIMA)
def test_a_maximum_of_zero_does_not_exclude_every_cell(field):
    """The foot-gun by name: the whole reason zero means 'no bound' here."""
    params = QCParams(enabled=True).with_updates(**{field: "0"})
    assert int(compute_qc_pass(_CELLS, params).sum()) == len(_CELLS)


def test_a_real_bound_still_bites():
    params = QCParams(enabled=True).with_updates(max_area="250")
    assert params.max_area == 250.0
    assert int(compute_qc_pass(_CELLS, params).sum()) == 2


def test_the_form_builds_its_params_through_with_updates_kwargs():
    """The guard: a bare ``.with_updates()`` after the constructor normalises
    nothing, because the loop inside it filters on ``k in changes``."""
    import ast
    import inspect
    import textwrap

    from review.features.qc import tab_view

    src = textwrap.dedent(inspect.getsource(tab_view.build_qc_gate_controls))
    tree = ast.parse(src)

    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "with_updates"]
    assert calls, "the QC form no longer normalises its bounds at all"
    for call in calls:
        assert call.keywords, (
            "with_updates() called with no keyword arguments normalises "
            "nothing — the bounds reach QCParams unnormalised, and a typed 0 "
            "in a maximum then excludes every cell"
        )
        names = {kw.arg for kw in call.keywords}
        assert set(_MAXIMA + _MINIMA) <= names, (
            f"bounds not routed through the normaliser: {sorted(set(_MAXIMA + _MINIMA) - names)}"
        )


def test_a_zero_bound_survives_a_round_trip_to_disk_as_no_bound():
    """``from_dict`` runs the same normalisation, so a value that behaved one
    way in the session and another way after a restart is the real hazard."""
    params = QCParams(enabled=True).with_updates(max_area="0")
    assert QCParams.from_dict(params.to_dict()).max_area is None
