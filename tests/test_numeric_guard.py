"""A numpy scalar is a number.

Every numeric guard on the plotting and export paths was spelled
``isinstance(value, (int, float)) and math.isfinite(value)``. That is False for
``numpy.float32``, ``numpy.float64`` and ``numpy.int64``, and the values
reaching those guards come out of numpy reductions several call levels away —
so the test asked which class had produced a number rather than whether it was
one. The failure is silent and always in the same direction: a real spread
becomes "no error bar", a real mean becomes a placeholder. CLAUDE.md §7.5 #8
records this exact class in the fold-change module, where a reviewer found it
after a differential test with hand-made fixtures had reported no difference.

Two predicates, because the distinction is load-bearing: ``is_number`` answers
"is this a usable measurement?" and rejects NaN, while ``is_numeric`` answers
"is this a number rather than a string, None or an array?" and lets NaN
through — a NaN spread means *undefined*, and folding it to 0.0 says the
replicates agreed exactly.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from review.domain.numeric import is_number, is_numeric  # noqa: E402
from tests._repo_scan import parse_source


@pytest.mark.parametrize("value", [
    np.float32(1.5), np.float64(2.0), np.int64(3), np.int32(4), np.uint8(5),
    1.0, 1, -0.0,
])
def test_a_real_scalar_number_is_a_number(value):
    assert is_number(value) is True
    assert is_numeric(value) is True


@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), -float("inf"),
    np.float32("nan"), np.float64("inf"),
])
def test_a_non_finite_value_is_numeric_but_not_a_number(value):
    assert is_number(value) is False
    assert is_numeric(value) is True, (
        "a NaN spread must stay distinguishable from a measured 0.0"
    )


@pytest.mark.parametrize("value", [
    None, "1.5", "", [1.0], (1.0,), {"a": 1},
    np.array([1.0]), np.array([1.0, 2.0]), np.array(1.0),
])
def test_nothing_else_passes(value):
    assert is_number(value) is False
    assert is_numeric(value) is False


def test_a_one_element_array_is_not_a_scalar():
    """``float(np.array([1.0]))`` succeeds, so the plain float() test is wider
    than the isinstance spelling it replaces. An array in a scalar slot is a
    bug and must not be converted quietly."""
    assert is_number(np.array([1.0])) is False


def test_a_float32_spread_survives_the_fold_change_helper():
    """The measured consequence: this helper is what decides whether a bar
    gets an error bar at all."""
    from review.domain.fold_change import _finite_spread

    assert _finite_spread(np.float32(0.25)) == pytest.approx(0.25)
    assert _finite_spread(float("nan")) == 0.0


def test_no_data_path_module_still_asks_which_class_produced_the_number():
    """The sweep, held: these modules judge measured values, not argument
    shapes, so an ``isinstance`` numeric test in one of them is the defect."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    swept = [
        "review/domain/fold_change.py",
        "review/features/export/service.py",
        "review/features/lineplot/controller.py",
        "review/features/barplot/controller.py",
        "review/features/batch_export/base_panel.py",
    ]
    offenders = []
    for rel in swept:
        path = root / rel
        tree = parse_source(path)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "isinstance" and len(node.args) == 2):
                arg = node.args[1]
                names = ({e.id for e in arg.elts if isinstance(e, ast.Name)}
                         if isinstance(arg, ast.Tuple) else set())
                if names == {"int", "float"}:
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        f"{offenders} test a value's class rather than whether it is a number; "
        "a numpy scalar fails that test and silently becomes 'not computed'"
    )
