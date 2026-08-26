"""Hovering the heat map must not write anything into the control band.

Moving the cursor over a cell used to stamp ``A01: 1234`` into a label that sat
in the tab's one control row, immediately after the Timepoint combo. Two things
were wrong with that. The readout duplicated the number already drawn on the
cell under the cursor, and because the label is a normal member of the band's
scope zone, the text it acquired on hover widened the row and pushed the
ON-threshold control out of reach — a control the user is reaching for while
their cursor is over the grid.

The fix is that mouse motion over the canvas drives the row/column label drag
and nothing else. This guard pins both halves of that: no widget in the heat-map
tab exists to hold such a readout, and the canvas motion dispatcher calls only
the label-drag handler.
"""

from __future__ import annotations

import ast
from pathlib import Path
from tests._repo_scan import parse_source

_HEATMAP_DIR = Path(__file__).resolve().parents[1] / "well_viewer" / "features" / "heatmap"


def _module_ast(name: str) -> ast.Module:
    path = _HEATMAP_DIR / name
    return parse_source(path)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def test_no_hover_status_widget_in_the_heatmap_tab():
    """No module in the feature registers or writes a hover status label."""
    offenders = []
    for path in sorted(_HEATMAP_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), start=1):
            if "_heatmap_status_lbl" in line:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "the heat-map hover readout is back in the control band:\n  "
        + "\n  ".join(offenders)
    )


def test_canvas_motion_only_drives_the_label_drag():
    """``_on_canvas_motion`` dispatches to the label drag and nothing else."""
    fn = _function(_module_ast("tab_view.py"), "_on_canvas_motion")
    called = {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called == {"on_heatmap_label_drag_motion"}, (
        "heat-map canvas motion should drive only the row/column label drag; "
        f"it also calls {sorted(called - {'on_heatmap_label_drag_motion'})}"
    )


def test_controller_exposes_no_hover_motion_handler():
    """The hover handler is gone rather than merely disconnected."""
    tree = _module_ast("controller.py")
    names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert "on_heatmap_motion" not in names, (
        "on_heatmap_motion is back; hovering a cell must not produce a readout"
    )
