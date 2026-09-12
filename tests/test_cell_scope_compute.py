"""The per-cell export's row rule, its marker columns, and its CSV shape.

These are the properties the round-trip tests depend on and cannot see: that a
narrowing is a column rather than a filter, that a marker reads back as 0 / 1 /
blank rather than as ``1.0``, that a verdict layer the app treats as all-pass is
written as ``1`` while a measurement nobody made is written blank, and that a
``#`` inside a filename does not truncate its row.
"""

from __future__ import annotations

import csv
import io

import numpy as np
import pandas as pd
import pytest

from review.features.export import cell_scope_compute as csc


def _scope(**kwargs) -> csc.CellScope:
    base = dict(
        plot="test",
        val_col="gfp_mean_intensity",
        ratios={},
        threshold=100.0,
        fluor_gates={},
    )
    base.update(kwargs)
    return csc.CellScope(**base)


def _frame(**overrides) -> pd.DataFrame:
    df = pd.DataFrame({
        "well": ["A01"] * 4,
        "fov": ["1"] * 4,
        "timepoint_hours": [0.0, 0.0, 24.0, 24.0],
        "nucleus_id": [1, 2, 3, 4],
        "area_px": [200.0, 210.0, 220.0, 230.0],
        "gfp_mean_intensity": [50.0, 150.0, 250.0, 350.0],
        "Included": [1, 1, 1, 1],
    })
    for key, value in overrides.items():
        df[key] = value
    return df


# ── the row rule ─────────────────────────────────────────────────────────────

def test_a_cutoff_narrows_the_marker_and_not_the_row_set():
    """The whole design: the cells a cutoff excluded stay in the file.

    They are the denominator the plotted fraction was computed against, so a
    file that dropped them could not reproduce the number on the figure.
    """
    scope = _scope(fluor_gates={"gfp": 100.0})
    df = _frame()
    assert csc.population_mask(df, scope).sum() == 4
    assert csc.in_population(df, scope).sum() == 3


def test_the_inclusion_layers_do_filter():
    scope = _scope()
    df = _frame(Included=[1, 0, 1, 0])
    assert csc.population_mask(df, scope).sum() == 2


def test_a_timepoint_filter_selects_only_that_timepoint():
    scope = _scope(timepoints=(24.0,))
    assert csc.population_mask(_frame(), scope).sum() == 2


def test_a_missing_required_column_contributes_no_rows():
    """Not "contributes rows with a blank" — the tab could not have drawn them."""
    scope = _scope(required_columns=("shape_centroid_row",))
    assert csc.population_mask(_frame(), scope).sum() == 0


def test_a_missing_gate_channel_column_empties_the_population_marker():
    """The aggregation drops the well and the FOV-pooled collector keeps it.

    Under one population plus markers that disagreement needs no resolving: no
    row of the well is inside the statistic either way, which is what the
    marker records — visibly, rather than by the well vanishing.
    """
    scope = _scope(fluor_gates={"nosuch": 10.0})
    df = _frame()
    assert csc.population_mask(df, scope).sum() == 4
    assert csc.in_population(df, scope).sum() == 0


def test_gate_on_replaces_the_threshold_when_the_tab_uses_it():
    """Scatter-gate thresholding partitions on a column, not on ``> threshold``.

    A marker computed the other way would contradict the bar it came from.
    """
    scope = _scope(on_col="Gate_On", threshold=-1.0)
    df = _frame(Gate_On=[0, 1, 0, 1])
    assert list(csc.above_threshold(df, scope)) == [False, True, False, True]


# ── the columns ──────────────────────────────────────────────────────────────

def _chunk(df, scope):
    return csc.build_chunk(df, scope, group="G", well_label="A01", well_name="ctrl")


def test_identity_columns_lead_and_do_not_shadow_the_frames_own_well():
    out = _chunk(_frame(), _scope())
    assert list(out.columns[:3]) == list(csc.IDENTITY_COLUMNS)
    assert "well" in out.columns and "well_label" in out.columns
    assert out["well_label"].iloc[0] == "A01"


def test_markers_are_nullable_int8_so_they_never_read_back_as_one_point_zero():
    out = _chunk(_frame(), _scope())
    for name in (csc.MARKER_IN_POPULATION, csc.MARKER_ABOVE_THRESHOLD):
        assert str(out[name].dtype) == csc.MARKER_DTYPE
    buf = io.StringIO()
    out.to_csv(buf, index=False, quoting=csv.QUOTE_NONNUMERIC)
    text = buf.getvalue()
    header = text.splitlines()[0].replace('"', "").split(",")
    for row in text.splitlines()[1:]:
        cells = next(csv.reader([row]))
        for name in (csc.MARKER_IN_POPULATION, csc.MARKER_ABOVE_THRESHOLD):
            assert cells[header.index(name)] in ("0", "1", "")


def test_an_absent_verdict_layer_is_written_as_pass_not_as_unknown():
    """``project_verdict_column`` drops a layer whose verdict raised, and every
    consumer then reads absent as all-pass. A blank would say "unknown" where
    the app means "passed"."""
    out = _chunk(_frame(), _scope())
    for col in csc.VERDICT_COLUMNS:
        assert col in out.columns
        assert set(out[col].dropna().unique()) == {1}


def test_a_verdict_layer_the_frame_carries_is_not_overwritten():
    """Gate_Pass with the gate layer off, because that is the one layer a scope
    can decline to filter on — so its zeros survive into the file and must
    still read as zeros rather than being replaced by the absent-means-pass
    default."""
    out = _chunk(_frame(Gate_Pass=[1, 0, 1, 0]), _scope(include_gate=False))
    assert list(out["Gate_Pass"]) == [1, 0, 1, 0]


def test_included_is_not_duplicated():
    """``load_well_csv`` adds an all-1 ``Included`` when the CSV lacks one and
    cell gating writes the real verdict into that same column, so the frame's
    is the live value — appending a second would duplicate the header."""
    out = _chunk(_frame(), _scope())
    assert list(out.columns).count("Included") == 1


def test_the_header_has_no_duplicates_and_keeps_first_seen_order():
    scope = _scope()
    header = csc.header_for([["well", "area_px"], ["well", "shape_solidity"]], scope)
    assert len(header) == len(set(header))
    assert header[:3] == list(csc.IDENTITY_COLUMNS)
    assert header.index("area_px") < header.index("shape_solidity")


def test_a_column_one_well_lacks_is_blank_for_that_well_never_zero():
    scope = _scope()
    header = csc.header_for([["well", "area_px"], ["well", "shape_solidity"]], scope)
    out = _chunk(_frame(), scope).reindex(columns=header)
    buf = io.StringIO()
    out.to_csv(buf, index=False, quoting=csv.QUOTE_NONNUMERIC)
    back = pd.read_csv(io.StringIO(buf.getvalue()))
    assert back["shape_solidity"].isna().all()


def test_missing_verdict_layers_are_reported_for_the_scope_block():
    every = list(csc.VERDICT_COLUMNS)
    assert csc.missing_verdict_layers([every, [c for c in every if c != "QC_Pass"]]) \
        == ["QC_Pass"]
    assert csc.missing_verdict_layers(
        [["QC_Pass", "Crop_Pass", "Gate_Pass"]] * 2) == []


# ── the CSV itself ───────────────────────────────────────────────────────────

def test_a_hash_in_a_filename_does_not_truncate_its_row():
    """``to_csv`` does not quote a field just because it holds a ``#``, and the
    scope block above the header means readers are told ``#`` starts a comment.
    Unquoted, ``plate#3_A1_f1.tif`` would take the rest of its row with it."""
    df = _frame()
    df["filename"] = ["plate#3_A01_f1.tif"] * 4
    out = _chunk(df, _scope())
    buf = io.StringIO()
    buf.write("# scope line\n")
    out.to_csv(buf, index=False, quoting=csv.QUOTE_NONNUMERIC)

    back = pd.read_csv(io.StringIO(buf.getvalue()), comment="#")
    assert len(back) == 4
    assert back["filename"].iloc[0] == "plate#3_A01_f1.tif"
    assert back["gfp_mean_intensity"].notna().all()


def test_values_survive_the_round_trip_exactly_with_round_trip_precision():
    """pandas writes a shortest-round-trippable repr but its default *reader*
    does not return it; the tests read with ``float_precision="round_trip"``
    for that reason, and this is what would fail if that were dropped."""
    rng = np.random.default_rng(0)
    values = rng.lognormal(mean=5.0, sigma=1.5, size=2000)
    buf = io.StringIO()
    pd.DataFrame({"v": values}).to_csv(buf, index=False)
    back = pd.read_csv(io.StringIO(buf.getvalue()), float_precision="round_trip")
    assert np.array_equal(back["v"].to_numpy(), values)


# ── categorical frames ───────────────────────────────────────────────────────

def test_a_categorical_slice_does_not_fabricate_absent_wells():
    """``load_well_csv`` stores the text columns as categories, and a slice
    keeps the unused ones — so a default ``groupby`` reports ``size() == 0``
    and ``mean() == NaN`` for wells that are not there. Anything grouping the
    exported rows has to pass ``observed=True``; this pins the hazard so the
    round-trip tests' recompute cannot silently acquire it."""
    df = _frame()
    df["well"] = pd.Categorical(["A01"] * 4, categories=["A01", "B02", "C03"])
    sub = df.loc[df["nucleus_id"] <= 2]
    assert len(sub.groupby("well", observed=False).size()) == 3
    assert len(sub.groupby("well", observed=True).size()) == 1


def test_the_exported_group_and_well_columns_are_plain_object():
    """Built fresh rather than reused from the frame's categorical ``well``, so
    a downstream ``groupby`` on them cannot inherit unused categories."""
    df = _frame()
    df["well"] = pd.Categorical(["A01"] * 4, categories=["A01", "B02"])
    out = _chunk(df, _scope())
    for col in csc.IDENTITY_COLUMNS:
        assert out[col].dtype == object


@pytest.mark.parametrize("column", ["group", "well_label", "well_name"])
def test_identity_columns_are_constant_per_well(column):
    out = _chunk(_frame(), _scope())
    assert out[column].nunique() == 1
