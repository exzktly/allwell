"""A nanowell timepoint the run left unaddressed is named as such, and opens straight onto setting its anchor.

Reported: "If wells have an unaddressed fiducial in nanowell it is not listed as such as its reason for failure in
QC triage or home page. It needs to be, so I can click on it and set the fiducial manually."

What QC Triage did with such a well, read from the record the real writer produces (``completeness.well_record``):
a well whose every timepoint was located but none addressed covers nothing, so it got one row — "Input is present
and nothing was written for it" — and none of its timepoints; a well with some addressed listed the others as
"In the input, absent from the measurements CSV". Neither says why, and the only action offered was a re-run, which
finds the same nothing. Home listed the well among *Partial wells* ("their CSVs cover fewer field groups") beside
its not-addressed row, whose wells were plain text.

Now each such timepoint is its own Triage row with the reason its grid record gives, and one an anchor can fix
offers *Set anchor…*; Home's not-addressed wells are buttons. Both open the Nanowells page's Array view on that
well and timepoint with *Set anchor* on — tested here through the real page, ending in the click that writes the
anchor.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pipeline import completeness as C
from review.features.home import collect as hc
from review.features.home.triage import collect as tc
from review.features.home.triage import controller as tcc
from tests.test_home_collect import _block, _ctx as _home_ctx
from tests.test_nanowell_completeness import _coverage, _well
from tests.test_qc_triage import _ctx as _triage_ctx, _run_check

REASON = "No accepted fiducial, array centre, or plate-rigid prediction"


def _record(tmp_path, statuses, reasons=None):
    """The run_check entry the pipeline writes for well A01, and its grid record carrying each timepoint's reason."""
    (tmp_path / "rec").mkdir(exist_ok=True)
    record = C.well_record(_coverage(_well(tmp_path / "rec", statuses)))
    grid = {"scheme_version": 1, "well": "A01",
            "timepoints": {tp: {"status": s, "reason": (reasons or {}).get(tp, "")} for tp, s in statuses.items()}}
    (tmp_path / "nanowells").mkdir(exist_ok=True)
    (tmp_path / "nanowells" / "A01_grid.json").write_text(json.dumps(grid))
    return record


def _a01(rows):
    return [(r.kind, r.timepoint, getattr(r, "can_anchor", None)) for r in rows if r.well == "A01"]


# ── QC Triage ────────────────────────────────────────────────────────────────

def test_a_well_with_no_timepoint_addressed_lists_each_with_its_reason_not_never_processed(tmp_path):
    record = _record(tmp_path, {"0h": "unaddressed", "24h": "unaddressed", "48h": "no_lattice"},
                     {"0h": REASON, "24h": REASON + ".", "48h": "No lattice was found"})
    rows = tc.triage_rows(_triage_ctx(tmp_path, {"run_check": _run_check({"A01": record})}))
    assert _a01(rows) == [(tc.KIND_UNADDRESSED, "0h", True), (tc.KIND_UNADDRESSED, "24h", True),
                          (tc.KIND_UNADDRESSED, "48h", False)]
    detail = {r.timepoint: r.detail for r in rows}
    assert detail["0h"] == (f"Nanowells not addressed (unaddressed): {REASON}. Set an anchor on its central square "
                            "by hand (Set anchor…), then re-run the well.")
    assert detail["24h"] == detail["0h"]                      # the record's own full stop is not doubled
    assert detail["48h"] == ("Nanowells not addressed (no lattice): No lattice was found. An anchor set by hand "
                             "cannot change this; re-running the well redoes it.")


def test_an_addressed_well_s_unaddressed_timepoint_replaces_its_missing_row(tmp_path):
    record = _record(tmp_path, {"0h": "ok", "24h": "unaddressed", "48h": "ok"}, {"24h": REASON})
    rows = tc.triage_rows(_triage_ctx(tmp_path, {"run_check": _run_check({"A01": record})}))
    assert _a01(rows) == [(tc.KIND_UNADDRESSED, "24h", True)]


def test_a_grid_record_that_cannot_be_read_still_names_the_status(tmp_path):
    record = _record(tmp_path, {"0h": "unaddressed"})
    (tmp_path / "nanowells" / "A01_grid.json").write_text("{not json")
    rows = tc.triage_rows(_triage_ctx(tmp_path, {"run_check": _run_check({"A01": record})}))
    assert [r.detail for r in rows if r.kind == tc.KIND_UNADDRESSED] == [
        "Nanowells not addressed (unaddressed): the grid record gives no reason. Set an anchor on its central "
        "square by hand (Set anchor…), then re-run the well."]


def test_set_anchor_asks_the_nanowells_page_for_that_well_and_timepoint(tmp_path):
    record = _record(tmp_path, {"0h": "ok", "24h": "unaddressed", "48h": "no_lattice"})
    ctx = _triage_ctx(tmp_path, {"run_check": _run_check({"A01": record})})
    asked = []
    ctx.features = SimpleNamespace(query=lambda key, *a, default=None: asked.append((key, a[1:])) or True)
    rows = {r.timepoint: r for r in tc.triage_rows(ctx) if r.well == "A01"}
    assert tcc.open_anchor(ctx, rows["24h"]) is True
    assert not tcc.open_anchor(ctx, rows["48h"])              # an anchor cannot change a missing lattice
    assert asked == [("nanowell.open_anchor", ("A01", "24h"))]


# ── Home ─────────────────────────────────────────────────────────────────────

def test_home_names_the_well_only_as_not_addressed_and_offers_its_first_anchorable_timepoint(tmp_path):
    record = _record(tmp_path, {"0h": "no_lattice", "24h": "unaddressed", "48h": "unaddressed"})
    partial = C.well_record(_coverage(_well(tmp_path / "b", {"0h": "ok", "24h": "ok"}))) \
        if (tmp_path / "b").mkdir() is None else None
    rows = {r["label"]: r for r in hc.health_rows(_home_ctx(_block({"A01": record, "A02": partial}),
                                                           wells=("A01", "A02")))}
    assert rows["Partial wells"]["wells"] == ["A02"]            # A01's whole shortfall is said by its own row
    assert rows["Nanowell timepoints not addressed"]["wells"] == ["A01"]
    assert rows["Nanowell timepoints not addressed"]["anchor"] == {"A01": "24h"}


def test_the_plate_tooltip_says_the_well_has_timepoints_not_addressed(tmp_path):
    from review.features.home.plate_panel import well_tooltip

    record = _record(tmp_path, {"0h": "unaddressed", "24h": "ok"})
    tip = well_tooltip("A01", {"A01": hc.CELL_PARTIAL}, {"A01": record})
    assert tip.endswith("1 nanowell timepoint(s) not addressed — set an anchor by hand")


# ── the Nanowells page, reached as Triage reaches it ────────────────────────

pytest.importorskip("PySide6")


class _Rail:
    """The rail's page stack, as far as the service uses it: showing the Nanowells page activates it, as the
    real stack's ``currentChanged`` does through ``centre_view``."""

    def __init__(self, ctx):
        self.ctx, self.shown = ctx, []

    def currentName(self):
        return self.shown[-1] if self.shown else "Home"          # QC Triage lives on Home

    def setCurrentByName(self, name):
        from review.features.nanowell import controller as nwc

        self.shown.append(name)
        nwc.nw_on_activate(self.ctx)
        return name == "Nanowells"


def test_triage_s_set_anchor_opens_the_array_view_with_set_anchor_on_and_a_click_writes_the_anchor(
        qtbot, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from review.features.home.triage.controller import open_anchor
    from tests.test_nanowell_full_montage import PHASE, PX, SCHEMA, SEP, SIDE, _block as grid_block, _tiles_zip
    from tests.test_nanowell_full_montage import _widget_point
    from tests.test_nanowell_page import _page, _write_nanowell_dataset
    from util.nanowell_anchors import anchors_path, read_anchors

    out = _write_nanowell_dataset(tmp_path, counts={"A01": {"t_c": [1, 2, 0, 1], "t_a": [2, 4, 3, 1]}})
    _tiles_zip(out.parent / "in" / "A01.zip")
    grid = json.loads((out / "nanowells" / "A01_grid.json").read_text())
    grid["timepoints"] = {"t_c": grid_block(), "t_a": grid_block("unaddressed", None, REASON)}
    (out / "nanowells" / "A01_grid.json").write_text(json.dumps(grid))
    info = json.loads((out / "pipeline_info.json").read_text())
    acq = {"pixel_size_um": {"x": PX, "y": PX}, "tile_size_px": {"x": SIDE, "y": SIDE}}
    info.update(acquisition={"t_c": acq, "t_a": acq}, schema=SCHEMA, separator=SEP, phase_token=PHASE)
    info["run_check"] = _run_check({"A01": {"health": "partial", "covered_any": 1, "missing": [["", "t_a"]],
                                            "missing_count": 1, "failed": [], "not_addressed_count": 1,
                                            "not_addressed": [["t_a", "unaddressed"]]}})
    (out / "pipeline_info.json").write_text(json.dumps(info))
    ctx = _page(qtbot, out)
    rail = _Rail(ctx)
    ctx.window._notebook = rail
    st = ctx.state.feature_slice("nanowell")
    assert st.view == "distribution" and "A01" not in ctx.state.selection.selected_wells

    from review.core.record_sync import resync_run_check

    assert resync_run_check(ctx)                     # Home reads the record so when it is shown
    row = next(r for r in tc.triage_rows(ctx) if r.kind == tc.KIND_UNADDRESSED)
    assert open_anchor(ctx, row)
    qtbot.waitUntil(lambda: st.metrics is not None and not st.computing and st.anchor_pending is None,
                    timeout=20000)
    assert rail.shown == ["Nanowells"]
    assert "A01" in ctx.state.selection.selected_wells
    assert (st.view, st.well, st.timepoint) == ("array", "A01", "t_a")
    assert st.anchor_mode and st.array_message == ""
    assert ctx.widgets.get("_nw_anchor_btn").isChecked()

    canvas, at = _widget_point(ctx, st, 30.0, 40.0)
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, at)
    anchors = read_anchors(anchors_path(out.parent / "in"))
    assert list(anchors) == [("A01", "t_a")]
    assert anchors[("A01", "t_a")].position() == pytest.approx((30.0, 40.0), abs=1.0)
