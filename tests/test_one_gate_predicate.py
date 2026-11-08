"""The gate predicate lives below the features and cannot be called without
saying where a nanowell gate's values come from (tracker G1, G2).

``review/domain/gates.py`` is asked by the Scatter tab, Cell Gating's pass and
the Cell Browser. Before it moved, Cell Gating and the Cell Browser reached it
through a lazy import of the Scatter feature, so a build without Scatter lost
the projector every loaded well goes through.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


import pandas as pd
import pytest

from review.domain.gates import compute_gate_inclusion

REPO = Path(__file__).resolve().parent.parent

_NW_GATE = {"x0": 5.0, "x1": 15.0, "y0": 0.0, "y1": 100.0,
            "x_col": "nw::count", "y_col": "area_px"}


def _cells():
    # Two cells in nanowell (0, 0), one in (0, 1); the nanowell counts are 10
    # and 20, so only the first two lie inside a gate of 5..15 on the count.
    return pd.DataFrame({"well": ["A01"] * 3, "timepoint": ["0h"] * 3,
                         "nw_q": [0, 0, 0], "nw_r": [0, 0, 1],
                         "area_px": [50.0, 60.0, 70.0]})


def _nanowells():
    return pd.DataFrame({"well": ["A01", "A01"], "timepoint": ["0h", "0h"],
                         "nw_q": [0, 0], "nw_r": [0, 1], "count": [10.0, 20.0]})


def test_leaving_out_the_nanowell_rows_is_an_error_not_a_silent_empty_gate():
    with pytest.raises(TypeError):
        compute_gate_inclusion(_cells(), [_NW_GATE])  # type: ignore[call-arg]


def test_a_nanowell_gate_reads_each_cell_s_own_nanowell():
    mask = compute_gate_inclusion(_cells(), [_NW_GATE], nanowell_values=_nanowells())
    assert mask.tolist() == [True, True, False]


def test_cell_gating_and_the_cell_browser_need_no_scatter_feature():
    """Run the predicate through both importers with the Scatter feature unimportable."""
    code = """
import sys, importlib.abc
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "review.features.scatter" or name.startswith("review.features.scatter."):
            raise ImportError("scatter is not in this build")
        return None
sys.meta_path.insert(0, Block())
import pandas as pd
from review.features.home.cell_gating import gating_compute
from review.features.image_browser.cell_browser import collect
df = pd.DataFrame({"a": [1.0, 5.0], "b": [1.0, 5.0]})
gate = {"x0": 0, "x1": 2, "y0": 0, "y1": 2, "x_col": "a", "y_col": "b"}
for mod in (gating_compute, collect):
    got = mod.compute_gate_inclusion(df, [gate], None, nanowell_values=None).tolist()
    assert got == [True, False], got
print("ok")
"""
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                          text=True, timeout=120,
                          env={"PYTHONPATH": str(REPO), "QT_QPA_PLATFORM": "offscreen",
                               "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0 and proc.stdout.strip() == "ok", proc.stderr[-2000:]

