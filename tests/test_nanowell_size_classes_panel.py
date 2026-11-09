"""The size classes through the real controls: typed into Cell Gating's panel, counted on the Nanowells page.

The pure counting is pinned by ``test_nanowell_size_classes.py``; this file drives the boxes a user types in
(CLAUDE.md §7.6) and reads what reaches the page — its metrics, its Metric menu and its figure — so a box wired to
nothing, a rule that never reaches the tally, or a label that never reaches a name each fail here.

Fixture: well A01 at two timepoints. Nanowell (0,0) holds a 100 px cell (T cells), a 400 px cell (DCs) and a 300 px
cell in the gap between the ranges; (1,0) holds two T-cell-sized cells and no DC. So the ratio is defined in one
nanowell and not the other, and a cell in the gap separates "unclassified" from "forced into a class".
"""

from __future__ import annotations

import json
import math
import re

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6")

PIXEL_UM = 0.5


def _dataset(tmp_path):
    from tests.test_nanowell_review_data import _dataset, _row

    rows = []
    for tp in ("t_c", "t_a"):
        rows += [_row(tp, 0, 0, n=3, c=0.2, gfp=100.0), _row(tp, 1, 0, n=2, c=0.1, gfp=50.0)]
    out = _dataset(tmp_path, {"A01": rows}, starts={"t_c": "2026-03-01T09:00:00", "t_a": "2026-03-01T14:00:00"})
    cells = [(tp, q, area) for tp in ("t_c", "t_a") for q, area in ((0, 100), (0, 400), (0, 300), (1, 90), (1, 120))]
    pd.DataFrame([{"well": "A01", "fov": "F0", "timepoint": tp, "nucleus_id": i + 1, "area_px": area,
                   "GFP_mean_intensity": 10.0, "nw_q": q, "nw_r": 0} for i, (tp, q, area) in enumerate(cells)]
                 ).to_csv(out / "measurements_A01.csv", index=False)
    info = json.loads((out / "pipeline_info.json").read_text())
    info["acquisition"] = {tp: {"pixel_size_um": {"x": PIXEL_UM, "y": PIXEL_UM}, "tile_size_px": {}}
                           for tp in ("t_c", "t_a")}
    (out / "pipeline_info.json").write_text(json.dumps(info))
    return out


def _site(st, tp, q):
    m = st.metrics
    return m[(m["well"] == "A01") & (m["timepoint"] == tp) & (m["nw_q"] == q)].iloc[0]


def _panel(qtbot, ctx):
    from PySide6.QtWidgets import QWidget

    from review.features.qc.tab_view import build_qc_gate_controls

    host = QWidget()
    qtbot.addWidget(host)
    build_qc_gate_controls(ctx, host)
    host.show()
    _HOSTS.append(host)                     # the panel lives as long as its host; the test must hold it
    return ctx.widgets.get("_qc_gate_widgets")


_HOSTS: list = []


@pytest.fixture(autouse=True)
def _release_hosts():
    yield
    _HOSTS.clear()


def _type(box, text):
    """Type into a box as a user does, and leave it: ``editingFinished`` is what applies a typed value."""
    box.setFocus()
    box.clear()
    box.insert(text)
    box.editingFinished.emit()


def _wait_current(qtbot, ctx, st):
    from review.features.nanowell import controller as nwc

    nwc.nw_on_activate(ctx)
    qtbot.waitUntil(lambda: not st.computing and st.gated_for == nwc.gate_version(ctx), timeout=20000)


def test_ranges_typed_on_cell_gating_are_counted_on_the_nanowells_page_under_the_user_s_labels(qtbot, tmp_path,
                                                                                             monkeypatch):
    from review.features.nanowell import controller as nwc
    from review.features.qc import controller as qcc
    from review.features.qc.controller import get_qc_params
    from review.domain.size_classes import SizeClassRule
    from tests.test_nanowell_page import _loaded, _page, _texts

    ctx = _page(qtbot, _dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    assert np.isnan(_site(st, "t_c", 0)["size_1"])               # nothing set yet: undefined, not zero
    w = _panel(qtbot, ctx)
    assert w["size_classes_row"].isVisible()                       # a nanowell dataset
    reprojected = []
    monkeypatch.setattr(qcc, "reproject_verdicts", lambda *a, **k: reprojected.append(1) or [])
    import review.features.home.cell_gating.controller as cgc

    replotted = []
    monkeypatch.setattr(cgc, "cell_gating_replot_cdfs", lambda _ctx: replotted.append(1))

    for key, text in (("size_label_0", "T cells"), ("size_min_0", "80"), ("size_max_0", "250"),
                      ("size_label_1", "DCs"), ("size_min_1", "350"), ("size_max_1", "1200")):
        _type(w[key], text)

    assert get_qc_params(ctx).size_classes == SizeClassRule.of(("T cells", 80, 250), ("DCs", 350, 1200))
    assert reprojected == []                                       # not a gate: no verdict was recomputed
    assert replotted                                               # the area curve shades the new ranges
    _wait_current(qtbot, ctx, st)
    here = _site(st, "t_c", 0)
    assert (here["size_1"], here["size_2"], here["size_unclassified"], here["size_ratio"]) == (1.0, 1.0, 1.0, 1.0)
    other = _site(st, "t_c", 1)
    assert (other["size_1"], other["size_2"]) == (2.0, 0.0) and math.isnan(other["size_ratio"])

    # The saved table names what each position counted, since its columns are size_1 / size_2.
    written = pd.read_csv(nwc.export_long_csv(st, tmp_path / "long.csv") and tmp_path / "long.csv")
    row = written[(written["timepoint"] == "t_c") & (written["nw_q"] == 0)].iloc[0]
    assert (row["size_1"], row["size_2"], row["size_unclassified"]) == (1, 1, 1)
    assert row["size_classes"] == "T cells 80–250 px, DCs 350–1200 px"

    # The readout under the rows: the diameter at the run's pixel size, and what the classes feed.
    assert w["size_diameter_0"].text() == f"≈ {2 * math.sqrt(80 / math.pi) * PIXEL_UM:.1f}–" \
                                          f"{2 * math.sqrt(250 / math.pi) * PIXEL_UM:.1f} µm"
    assert w["size_classes_status"].text().startswith("Each nanowell's kept cells are counted as T cells, DCs or "
                                                      "neither")

    # The page names the classes by the labels typed, in its Metric menu and on its figure.
    st.metric = "size_ratio"
    nwc.redraw(ctx)
    combo = ctx.widgets.get("_nw_metric_cb")
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert "T cells count" in texts and "T cells : DCs ratio" in texts and "Size class 1 count" not in texts
    said = re.sub(r"\s+", " ", " ".join(_texts(ctx)))
    assert "Size classes (Cell Gating, cells it keeps): T cells 80–250 px, DCs 350–1200 px." in said
    assert "fall in neither class" in said
    assert "The line joins each well's mean" in said

    # A Subset condition on a size metric is written on the figure with the user's label too.
    from review.features.nanowell import subset_compute as ss

    st.subset = ss.Subset((ss.Condition("size_1", "ge", 2.0, at="t_c"),))
    nwc.redraw(ctx)
    said = re.sub(r"\s+", " ", " ".join(_texts(ctx)))
    assert "T cells count" in said and "Size class 1" not in said


def test_an_incomplete_rule_says_what_is_missing_and_leaves_the_metrics_undefined(qtbot, tmp_path):
    from review.features.qc.controller import get_qc_params
    from tests.test_nanowell_page import _loaded, _page

    ctx = _page(qtbot, _dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    w = _panel(qtbot, ctx)
    _type(w["size_label_0"], "T cells")
    _type(w["size_max_0"], "250")
    status = w["size_classes_status"]
    assert status.text().startswith("Class 2 needs a label.") and status.objectName() == "Notice"
    assert not get_qc_params(ctx).size_classes.active()
    _wait_current(qtbot, ctx, st)
    assert np.isnan(_site(st, "t_c", 0)["size_1"])


def test_a_reload_refills_the_boxes_from_the_saved_rule(qtbot, tmp_path):
    from dataclasses import replace

    from review.domain.size_classes import SizeClassRule
    from review.features.qc.controller import get_qc_params
    from review.features.qc.tab_view import qc_gate_refresh_widgets
    from tests.test_nanowell_page import _page

    ctx = _page(qtbot, _dataset(tmp_path))
    w = _panel(qtbot, ctx)
    ctx.state.qc.params = replace(get_qc_params(ctx),
                                  size_classes=SizeClassRule.of(("small", None, 60), ("large", 500, None)))
    qc_gate_refresh_widgets(ctx.window)
    assert [w[k].text() for k in ("size_label_0", "size_min_0", "size_max_0", "size_label_1", "size_min_1",
                                  "size_max_1")] == ["small", "", "60", "large", "500", ""]
