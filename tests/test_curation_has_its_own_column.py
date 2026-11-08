"""Hand curation writes ``Curation_Pass``, not the ``Included`` the intensity gate writes (tracker C1–C4).

Before the split a cell deleted in Segmentation and a cell below the intensity
gate were the same ``Included = 0``: the funnel could only say "Curation +
intensity gate", the overlay called gate-dropped cells "deleted here", and a
reloaded well kept its curation only because the curation projector was
registered after the gate's. These drive the real projector on a real row
cache, in both registration orders.
"""

from __future__ import annotations

import pandas as pd
import pytest

from review.data.curation import CURATION_COLUMN, apply_curation_to_cache
from review.data.data_loading import VERDICT_LAYERS, df_included_mask


class _Rows(dict):
    """A row cache the projector can read and write, keyed by well."""


@pytest.fixture
def ctx():
    from types import SimpleNamespace

    rows = _Rows()
    rows["A01"] = pd.DataFrame({
        "fov": ["F001"] * 4, "timepoint": ["0h"] * 4, "nucleus_id": [1, 2, 3, 4],
        "Included": [1, 1, 0, 1]})            # nucleus 3 is below the intensity gate
    dataset = SimpleNamespace(well_paths={"A01": "x"}, note_rows_changed=lambda: None)
    state = SimpleNamespace(dataset=dataset, repo=SimpleNamespace(rows_or_none=rows.get))
    runtime = SimpleNamespace(review=SimpleNamespace(included_overrides={}))
    c = SimpleNamespace(state=state, runtime=runtime, _rows=rows)
    return c


def _curate(ctx, nid, value="0"):
    from domain.image_identity import CellKey
    key = CellKey.from_row({"fov": "F001", "timepoint": "0h", "nucleus_id": nid}, well="A01")
    ctx.runtime.review.included_overrides[key.as_tuple()] = value


def test_a_deletion_writes_its_own_column_and_leaves_the_gate_s(ctx):
    _curate(ctx, 2)
    apply_curation_to_cache(ctx)
    df = ctx._rows["A01"]
    assert df[CURATION_COLUMN].tolist() == [1, 0, 1, 1]
    assert df["Included"].tolist() == [1, 1, 0, 1]       # the gate's verdict, untouched
    assert df_included_mask(df).tolist() == [True, False, False, True]


def test_curation_survives_the_gate_rewriting_included_in_either_order(ctx):
    """The gate pass rewrites ``Included`` wholesale. With curation in that
    same column, running the gate after the curation projector erased the
    deletion; in a column of its own the order does not matter."""
    _curate(ctx, 2)
    apply_curation_to_cache(ctx)
    df = ctx._rows["A01"]
    df["Included"] = [1, 1, 1, 1]                        # a gate pass that now keeps everything
    assert df_included_mask(df).tolist() == [True, False, True, True]


def test_a_well_with_no_curation_carries_no_column(ctx):
    apply_curation_to_cache(ctx)
    assert CURATION_COLUMN not in ctx._rows["A01"].columns


def test_an_old_include_override_still_forces_the_cell_in(ctx):
    """No surface writes ``"1"`` any more; a file saved when one did keeps its meaning."""
    _curate(ctx, 3, "1")
    apply_curation_to_cache(ctx)
    df = ctx._rows["A01"]
    assert df.loc[2, "Included"] == 1 and df.loc[2, CURATION_COLUMN] == 1


def test_the_chokepoint_and_the_export_rule_name_the_layer():
    from review.features.export.cell_scope_compute import OPTIONAL_VERDICT_COLUMNS, inclusion_rule

    assert VERDICT_LAYERS[0] == CURATION_COLUMN
    assert CURATION_COLUMN in OPTIONAL_VERDICT_COLUMNS
    assert inclusion_rule(True).startswith("Included AND Curation_Pass AND QC_Pass")
