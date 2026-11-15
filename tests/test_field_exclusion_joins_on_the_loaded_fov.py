"""A flagged or hand-excluded field drops its cells whatever way the run spelled the field.

The full-app review (pull request #1343), row B1. The run's field-quality block and the
hand-exclusion map key a field by the raw filename token; ``load_well_csv`` normalises the frame's ``fov``
(``"01"`` -> ``"1"``, no field slot -> ``"1"``). Joined as written, a zero-padded or field-less plate matched
nothing: the panel read "Currently excluding 1 field" while every cell of that field stayed included.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from pipeline.field_quality import FIELD_QUALITY_BLOCK
from review.data import field_exclusions as fx
from review.data import field_quality_cache as fq
from review.data.data_loading import df_included_mask, load_well_csv
from review.domain.qc_params import QCParams

WELLS = tuple(f"{r}{c:02d}" for r in "ABCD" for c in range(1, 7))
CELLS_PER_FIELD = 3


def _ctx(tmp_path, fov_tokens, tps):
    """A plate as the pipeline writes it: the block and the CSVs carry the same raw field token."""
    wells, cache = {}, {}
    for wi, well in enumerate(WELLS):
        fields = [{"fov": f, "timepoint": t, "focus": 27.0 + wi * .05 + i * .01, "sat_frac": 0.0,
                   "dyn_range": 900.0 + i + ti, "nuc_count": 300 + i + ti, "nuc_area_median": 150.0 + i + ti,
                   "bg_level": 100.0 + i + ti}
                  for ti, t in enumerate(tps) for i, f in enumerate(fov_tokens)]
        if well == "A02":
            fields[len(fields) // 2]["focus"] = 2.0          # one badly defocused field
        wells[well] = {"well": well, "fields": fields, "measured": len(fields), "measured_of": len(fields)}
        rows = [{"well": well, "fov": f, "timepoint": t, "timepoint_hours": 0.0, "nucleus_id": k,
                 "area_px": 100, "gfp_mean_intensity": 50.0}
                for t in tps for f in fov_tokens for k in range(1, CELLS_PER_FIELD + 1)]
        path = tmp_path / f"{well}.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        cache[well] = load_well_csv(path)
    block = {"wells": wells, "partial_wells": [], "measured": 0, "measured_of": 0}
    return SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(pipeline_info={FIELD_QUALITY_BLOCK: block}, fov_integrity={}, cache=cache,
                                well_paths=dict.fromkeys(cache), field_exclusions={}),
        qc=SimpleNamespace(params=QCParams(exclude_flagged_fields=True))))


LAYOUTS = [
    pytest.param(("1", "2", "3"), ("0",), id="plain"),
    pytest.param(("01", "02", "03"), ("0",), id="zero-padded"),
    pytest.param(("",), ("t0", "t1", "t2"), id="no-field-slot"),
]


@pytest.mark.parametrize("fov_tokens, tps", LAYOUTS)
def test_a_flagged_field_s_cells_are_excluded(tmp_path, fov_tokens, tps):
    ctx = _ctx(tmp_path, fov_tokens, tps)
    flagged, refusal = fq.flagged_fields(ctx)
    assert not refusal and len(flagged) == 1, "the fixture flags exactly one field of A02"

    fq.apply_field_quality_to_cache(ctx)

    frame = ctx.state.dataset.cache["A02"]
    assert int((frame["Field_Pass"] == 0).sum()) == CELLS_PER_FIELD
    assert int(df_included_mask(frame).sum()) == len(frame) - CELLS_PER_FIELD
    assert "Field_Pass" not in ctx.state.dataset.cache["A01"].columns or \
        int((ctx.state.dataset.cache["A01"]["Field_Pass"] == 0).sum()) == 0


@pytest.mark.parametrize("fov_tokens, tps", LAYOUTS)
def test_a_hand_excluded_field_s_cells_are_excluded(tmp_path, fov_tokens, tps):
    ctx = _ctx(tmp_path, fov_tokens, tps)
    flagged, _refusal = fq.flagged_fields(ctx)
    ctx.state.dataset.field_exclusions = {key: {"reason": "out of focus"} for key in flagged}

    fx.apply_field_exclusions_to_cache(ctx)

    frame = ctx.state.dataset.cache["A02"]
    assert int((frame["Excluded_Field_Pass"] == 0).sum()) == CELLS_PER_FIELD
