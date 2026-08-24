"""One answer to "is there anything for this tab to draw?".

``core.selection_controller.has_plottable_selection`` exists so the empty-state
placeholder and the figure behind it cannot disagree, and its docstring said so.
But nothing had been swept: all four renderers still spelled the condition out
as ``if not selected and not active_rsets``, and the shared predicate's only
callers were Spatial, smFISH and a ui_helpers shim. A docstring describing a
consolidation that never happened is worse than no docstring — it tells the next
reader the copies are gone.

The predicate is subtler than the inline version reads, too: it filters by
``well_paths``, because a stale label a saved heat-map layout pushed through
``set_selected_wells`` plots nothing, and counting it as "plottable" hides the
placeholder over an empty figure.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]

_RENDERERS = [
    "well_viewer/features/barplot/renderer.py",
    "well_viewer/features/lineplot/controller.py",
    "well_viewer/features/scatter/controller.py",
]


@pytest.mark.parametrize("rel", _RENDERERS)
def test_no_renderer_spells_the_condition_out_itself(rel):
    src = (_ROOT / rel).read_text()
    tree = ast.parse(src, filename=rel)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.And):
            continue
        names = set()
        for operand in node.values:
            if (isinstance(operand, ast.UnaryOp)
                    and isinstance(operand.op, ast.Not)
                    and isinstance(operand.operand, ast.Name)):
                names.add(operand.operand.id)
        if any("rset" in n for n in names) and any(
                "select" in n or "wells" in n for n in names):
            offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        f"{offenders} re-implements has_plottable_selection. Two copies of "
        "this decision is how the placeholder and the plot came to disagree."
    )


@pytest.mark.parametrize("rel", _RENDERERS)
def test_every_renderer_asks_the_shared_predicate(rel):
    src = (_ROOT / rel).read_text()
    assert "has_plottable_selection" in src, (
        f"{rel} no longer asks the shared predicate whether there is anything "
        "to draw"
    )


def test_the_predicate_filters_by_well_paths():
    """The half the inline copies got wrong first: a stale label plots nothing."""
    import types

    from well_viewer.core.selection_controller import has_plottable_selection

    def _ctx(selected, well_paths, selections=()):
        return types.SimpleNamespace(state=types.SimpleNamespace(
            selection=types.SimpleNamespace(
                selected_wells=list(selected), selections=list(selections)),
            dataset=types.SimpleNamespace(well_paths=dict(well_paths)),
        ))

    assert has_plottable_selection(_ctx(["A01"], {"A01": "x"})) is True
    assert has_plottable_selection(_ctx(["Z99"], {"A01": "x"})) is False
    assert has_plottable_selection(_ctx([], {"A01": "x"})) is False
