"""Excluding cells the image border cuts (MATLAB's ``imclearborder``).

Two halves: the pipeline flag (``qc_touches_border``) and the Review layer that
turns it into ``Border_Pass``. Each test names the wrong implementation it
excludes, because every failure this feature can have is quiet — a flag off by
one pixel, a gate that filters nothing, a checkbox wired to nothing.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.shape_features import BORDER_COLUMN, labels_touching_border
from review.data.border_gate import (
    BORDER_PASS_COLUMN,
    apply_border_gate_to_cache,
    border_status,
    compute_border_pass,
    plate_border_counts,
)
from review.domain.qc_params import QCParams


# ── the pipeline's flag ─────────────────────────────────────────────────────

def _labels():
    """Six cells, one per case the rule must decide.

    1 top edge, 2 bottom edge, 3 left edge, 4 right edge, 5 interior but one
    pixel in from the left edge, 6 a single corner pixel.
    """
    lab = np.zeros((10, 12), dtype=np.int32)
    lab[0:2, 4:6] = 1
    lab[8:10, 4:6] = 2
    lab[4:6, 0:2] = 3
    lab[4:6, 10:12] = 4
    lab[4:6, 1:3][:, 1:] = 5          # column 2 only: one pixel off the edge
    lab[9, 0] = 6
    return lab


def test_exactly_the_cells_on_the_outermost_rows_and_columns_are_flagged():
    """Asserts the set, not a count. Excludes a strip one pixel too deep (which
    flags cell 5), a check of only two edges (misses 2 or 4), and a check that
    reads ``lab[-1]`` as a row but forgets the last column (misses 4)."""
    assert labels_touching_border(_labels()) == {1, 2, 3, 4, 6}


def test_background_is_never_reported_and_an_empty_image_flags_nothing():
    assert labels_touching_border(np.zeros((4, 4), np.int32)) == set()
    assert labels_touching_border(np.zeros((0, 0), np.int32)) == set()


def test_skimage_clear_border_agrees():
    """The reference implementation, on a random label image. Excludes any
    private reading of "touches" that differs from the one users know."""
    skimage = pytest.importorskip("skimage")
    from skimage.segmentation import clear_border

    rng = np.random.default_rng(3)
    lab = skimage.measure.label(rng.random((40, 50)) > 0.6).astype(np.int32)
    kept = set(np.unique(clear_border(lab))) - {0}
    every = set(np.unique(lab)) - {0}
    assert labels_touching_border(lab) == every - kept


def test_the_quantifier_writes_the_flag_per_cell():
    """The flag reaches the record the CSV is written from — not just the
    helper. Excludes a helper nothing calls."""
    pytest.importorskip("scipy")
    pytest.importorskip("skimage")
    from process_microscopy import _quantify_image_group
    from util.schema import parse_schema

    lab = _labels()
    img = np.ones(lab.shape, np.float32)
    records = _quantify_image_group(
        labels=lab, fluor_tophat=[img], fluor_tokens=["gfp"], nir_corr=img,
        nuclear_path=Path("exp_nuc_A01_f01_T01.tif"),
        schema=parse_schema("experiment:channel:well:fov:timepoint"), sep="_",
        shape_features=[], measure_focus=False)
    got = {r["nucleus_id"]: r[BORDER_COLUMN] for r in records}
    assert got == {1: 1, 2: 1, 3: 1, 4: 1, 5: 0, 6: 1}


# ── the Review verdict ──────────────────────────────────────────────────────

def _frame(flags):
    df = pd.DataFrame({"nucleus_id": list(range(1, len(flags) + 1)),
                       "area_px": [100] * len(flags), "Included": 1})
    df[BORDER_COLUMN] = [np.nan if f is None else f for f in flags]
    return df


ON = QCParams(exclude_border_cells=True)


def test_a_border_cell_is_dropped_and_an_interior_one_kept():
    """Position by position. Excludes the gate that drops nothing and the one
    that drops everything, and the inverted test (``eq(0)``)."""
    assert list(compute_border_pass(_frame([0, 1, 0, 1]), ON)) == [1, 0, 1, 0]


def test_an_unmeasured_cell_is_kept_and_the_switch_off_drops_nothing():
    assert list(compute_border_pass(_frame([None, 1]), ON)) == [1, 0]
    assert list(compute_border_pass(_frame([1, 1]), QCParams())) == [1, 1]


def test_it_does_not_need_the_debris_gate_switched_on():
    """``enabled`` is the debris gate's switch. Excludes the implementation
    that folded this into ``QCParams.is_active`` and so did nothing until a
    debris bound was set."""
    p = QCParams(enabled=False, exclude_border_cells=True)
    assert list(compute_border_pass(_frame([1, 0]), p)) == [0, 1]


def test_the_chokepoint_ands_the_border_column():
    """Excludes a column written and never read."""
    from review.data.data_loading import df_included_mask

    df = pd.DataFrame({"Included": [1, 1, 1], "QC_Pass": [1, 1, 0],
                       BORDER_PASS_COLUMN: [1, 0, 1]})
    assert list(df_included_mask(df)) == [True, False, False]


def test_the_setting_survives_a_save_and_reload():
    assert QCParams.from_dict(ON.to_dict()).exclude_border_cells is True
    assert QCParams.from_dict({}).exclude_border_cells is False


class _Ctx:
    def __init__(self, cache, params=None):
        self.state = type("S", (), {})()
        self.state.dataset = type("D", (), {})()
        self.state.dataset.cache = cache
        self.state.dataset.well_paths = dict.fromkeys(cache)
        self.state.qc = type("Q", (), {})()
        self.state.qc.params = params


def test_the_projector_writes_and_then_removes_its_column():
    """Removing rather than writing 1s when switched off: a frame must not keep
    being filtered by a control the user has just unticked."""
    ctx = _Ctx({"A01": _frame([0, 1])}, ON)
    assert apply_border_gate_to_cache(ctx) == []
    assert list(ctx.state.dataset.cache["A01"][BORDER_PASS_COLUMN]) == [1, 0]
    ctx.state.qc.params = QCParams()
    apply_border_gate_to_cache(ctx)
    assert BORDER_PASS_COLUMN not in ctx.state.dataset.cache["A01"].columns


def test_the_plate_counts_exclude_cells_other_layers_already_dropped():
    """Counted over what the analysis keeps without this layer. The QC-dropped
    border cell is the one a count over every row would include."""
    a = _frame([1, 1, 0])
    a["QC_Pass"] = [1, 0, 1]
    ctx = _Ctx({"A01": a, "A02": _frame([0, 1])}, ON)
    assert plate_border_counts(ctx) == (2, 4, 4)


def test_a_dataset_that_predates_the_column_says_so():
    """"None touch the border" is true of a plate nobody checked too — the two
    must not read alike. Amber only when the box is ticked."""
    plain = pd.DataFrame({"nucleus_id": [1], "area_px": [5]})
    text, problem = border_status(_Ctx({"A01": plain}, QCParams()))
    assert "before border contact was recorded" in text and problem is False
    _text, problem = border_status(_Ctx({"A01": plain}, ON))
    assert problem is True


def test_the_sentence_counts_and_says_what_the_switch_is_doing():
    ctx = _Ctx({"A01": _frame([1, 0, 0])}, QCParams())
    assert border_status(ctx) == (
        "1 cell of 3 touches the image border — currently kept, across the plate.",
        False)
    ctx.state.qc.params = ON
    ctx.state.dataset.cache["A02"] = _frame([1])
    ctx.state.dataset.well_paths["A02"] = None
    assert border_status(ctx) == (
        "2 cells of 4 touch the image border — currently excluded, across "
        "the plate.", False)


# ── a plate only partly analysed with the column (review of #999, M3) ───────

def _merged_well(tmp_path):
    """The reviewer's case through the pipeline's own writer: a well analysed
    before the column existed (100 cells), then continued by an ``--incremental``
    run after it (10 cells, 3 on the border). ``merge_records_into_gz_csv``
    adds the column with the old rows blank."""
    from pipeline.io import merge_records_into_gz_csv

    csv = tmp_path / "cells_A1.csv"
    merge_records_into_gz_csv(csv, [
        dict(well="A1", fov="1", timepoint="T0", nucleus_id=i, area_px=100.0)
        for i in range(1, 101)])
    merge_records_into_gz_csv(csv, [
        dict(well="A1", fov="2", timepoint="T0", nucleus_id=i, area_px=100.0,
             **{BORDER_COLUMN: int(i <= 3)}) for i in range(1, 11)])
    df = pd.read_csv(csv.with_suffix(".csv.gz"))
    assert int(df[BORDER_COLUMN].isna().sum()) == 100, "fixture: the old rows are blank"
    return df


def test_cells_analysed_before_the_column_are_not_counted_as_checked(tmp_path):
    """The panel said "3 cells of 110 touch the image border — currently
    excluded, across the plate" while 100 of the 110 were never checked:
    degraded output that read exactly like a fully checked plate. The count of
    unchecked cells, and the remedy that actually records them, must be said."""
    ctx = _Ctx({"A01": _merged_well(tmp_path)}, ON)

    assert plate_border_counts(ctx) == (3, 10, 110)
    assert border_status(ctx) == (
        "Border contact was checked for 10 of 110 cells: 3 cells touch the "
        "image border — currently excluded. The other 100 cells were analysed "
        "before it was recorded and are kept unjudged; Force re-run the "
        "analysis to record it.", True)


def test_a_well_without_the_column_is_counted_as_unchecked_not_dropped():
    """A whole well analysed before the column existed counted (0, 0) and
    vanished from a total the panel calls "across the plate"."""
    plain = pd.DataFrame({"nucleus_id": [1, 2], "area_px": [5, 5]})
    ctx = _Ctx({"A01": plain, "A02": _frame([1, 0])}, QCParams())

    assert plate_border_counts(ctx) == (1, 2, 4)
    text, problem = border_status(ctx)
    assert text.startswith("Border contact was checked for 2 of 4 cells: 1 cell "
                           "touches the image border — currently kept.")
    assert problem is False, "the box is unticked, so nothing asked for is missing"


def test_a_plate_with_none_checked_says_it_predates_the_measurement():
    """A column that exists and is blank on every row checked nothing."""
    ctx = _Ctx({"A01": _frame([None, None])}, ON)
    text, problem = border_status(ctx)
    assert "before border contact was recorded" in text and problem is True
    assert "Force re-run" in text, (
        "a plain re-run skips every well whose output exists, so it records "
        "nothing; only Force does")


def test_the_plate_count_reads_each_well_narrowly(monkeypatch):
    """Every QC change re-counts the whole plate (the stats cache it is
    memoized in is cleared by ``recompute_qc_all``). Read whole, that is a full
    frame read of every well, evicting the wells a view holds. Asserted at the
    read boundary: which reader was called, and with which columns."""
    import review.data.plate_scan as ps

    reads = []

    def whole(_ctx, label):
        reads.append(("get_rows", label, None))
        return _frame([1, 0])

    def narrow(_ctx, label, want):
        reads.append(("narrow_rows", label, frozenset(want)))
        return _frame([1, 0])

    monkeypatch.setattr(ps, "get_rows", whole)
    monkeypatch.setattr(ps, "narrow_rows", narrow)
    ctx = _Ctx(dict.fromkeys(("A01", "A02", "A03")), ON)
    ctx.state.dataset.cache = {}

    assert plate_border_counts(ctx) == (3, 6, 6)
    assert reads == [("narrow_rows", w, frozenset({BORDER_COLUMN}))
                     for w in ("A01", "A02", "A03")]


# ── the control itself (§7.6) ───────────────────────────────────────────────

def test_ticking_the_box_excludes_the_border_cells(qtbot):
    """The real ``build_qc_gate_controls`` on a real registry: tick the box and
    assert the column the projector writes, not only the params. Excludes a
    checkbox wired to nothing and a projector registered nowhere."""
    from tests._panel_jobs import wait_for_panel_jobs
    from tests.test_focus_gate import _stub_row
    from review.data.data_loading import df_included_mask
    from review.features.qc.tab_view import qc_gate_refresh_widgets

    app = _stub_row(qtbot, {"A01": _frame([0, 1, 0])}, QCParams())
    box = app._qc_gate_widgets["exclude_border_cells"]
    assert box.isChecked() is False
    box.setChecked(True)
    assert app._app_state.qc.params.exclude_border_cells is True
    # The re-projection runs behind the panel's progress popup.
    wait_for_panel_jobs(qtbot, app._ctx)
    df = app._app_state.dataset.cache["A01"]
    assert list(df[BORDER_PASS_COLUMN]) == [1, 0, 1]
    assert list(df_included_mask(df)) == [True, False, True]
    assert "currently excluded" in app._qc_gate_widgets["border_status"].text()

    box.setChecked(False)
    wait_for_panel_jobs(qtbot, app._ctx)
    assert BORDER_PASS_COLUMN not in app._app_state.dataset.cache["A01"].columns

    app._app_state.qc.params = ON
    qc_gate_refresh_widgets(app)
    assert box.isChecked() is True


# ── the default: on, except for nanowell runs ───────────────────────────────

_NANOWELL_INFO = {"nanowell": {"scheme_version": 1, "settings": {"pitch_um": 50}}}


def test_an_ordinary_dataset_opens_with_the_switch_on():
    assert QCParams.from_pipeline_info({}).exclude_border_cells is True
    assert QCParams.from_pipeline_info({"qc_options": {}}).exclude_border_cells is True


def test_a_nanowell_dataset_opens_with_the_switch_off():
    """Excludes the default applied regardless of run kind. The sidecar here is
    otherwise identical to the ordinary one above, so only the nanowell block
    can be what turns it off."""
    p = QCParams.from_pipeline_info({"qc_options": {}, **_NANOWELL_INFO})
    assert p.exclude_border_cells is False


@pytest.mark.parametrize("block, expected", [(_NANOWELL_INFO, False), ({}, True)],
                         ids=["nanowell", "ordinary"])
def test_the_default_is_decided_from_the_sidecar_as_the_app_reads_it(tmp_path, block, expected):
    """Through the reader the app loads with, not a hand-built dict.

    ``dataset.pipeline_info`` is not the file: ``read_pipeline_info`` builds a
    whitelist of the keys it knows, and it did not know ``nanowell``. Every
    test above handed ``from_pipeline_info`` a dict holding the block, so the
    nanowell default was green here and dead in the app, where a nanowell run
    opened with the switch on like any other plate."""
    import json

    from review.domain.viewer_state import read_pipeline_info

    out = tmp_path / "out"
    out.mkdir()
    (out / "pipeline_info.json").write_text(json.dumps({
        "schema": "experiment:channel:well:fov:timepoint", "fov_index": 3,
        "tp_index": 4, "well_index": 2, "fluor_tokens": ["gfp"], **block}))
    info = read_pipeline_info(out)[3]
    assert QCParams.from_pipeline_info(info).exclude_border_cells is expected


def test_a_null_nanowell_entry_is_not_a_nanowell_run():
    assert QCParams.from_pipeline_info({"nanowell": None}).exclude_border_cells is True


def _persisted_ctx(monkeypatch, section, info):
    from types import SimpleNamespace

    from review.persistence import _doc

    monkeypatch.setattr(_doc, "get_section", lambda _ctx, _key: section)
    st = SimpleNamespace(
        dataset=SimpleNamespace(data_dir="/x", pipeline_info=info),
        qc=SimpleNamespace(params=None))
    return SimpleNamespace(state=st, window=SimpleNamespace(_app_state=st))


@pytest.mark.parametrize("info, expected", [({}, True), (_NANOWELL_INFO, False)])
def test_a_section_saved_before_the_switch_existed_gets_the_dataset_default(
        monkeypatch, info, expected):
    """Every dataset whose panel was ever saved has a ``qc_params`` section
    without this key. Excludes the loader that lets ``from_dict`` read the
    absent key as off, which would hold all of them out of the default."""
    from review.persistence.qc_params import load_from_data_dir

    ctx = _persisted_ctx(monkeypatch, {"enabled": True, "min_area": 30}, info)
    load_from_data_dir(ctx)
    assert ctx.state.qc.params.exclude_border_cells is expected
    assert ctx.state.qc.params.min_area == 30


@pytest.mark.parametrize("saved", [True, False])
def test_a_saved_choice_wins_over_the_default(monkeypatch, saved):
    """The user's own tick or untick survives a reload, in both directions —
    including unticking on an ordinary plate and ticking on a nanowell one."""
    from review.persistence.qc_params import load_from_data_dir

    for info in ({}, _NANOWELL_INFO):
        ctx = _persisted_ctx(monkeypatch, {"exclude_border_cells": saved}, info)
        load_from_data_dir(ctx)
        assert ctx.state.qc.params.exclude_border_cells is saved
