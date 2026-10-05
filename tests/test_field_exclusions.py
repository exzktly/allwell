"""The field-exclusion inclusion layer (``review/data/field_exclusions.py``)
and the two persistence blocks QC triage writes.

Kept apart from ``tests/test_qc_triage.py`` because these outlive the tab: a
build that strips QC triage still honours the exclusions on disk, so these
tests must survive the profile that deletes that file.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from review.core.dataset_state import DatasetState
from review.data import field_exclusions as fx
from review.data.data_loading import VERDICT_LAYERS, df_included_mask
from review.persistence import field_exclusions as pfx
from review.persistence import triage_decisions as ptd


def _ctx(tmp_path, *, integrity=None):
    ds = DatasetState()
    ds.begin_load(tmp_path)
    ds.fov_integrity.update(integrity or {})
    return SimpleNamespace(state=SimpleNamespace(dataset=ds))


# ── the exclusion layer ─────────────────────────────────────────────────────

def _frame(well, fovs=("1", "2", "3")):
    return pd.DataFrame({"well": [well] * len(fovs), "fov": list(fovs),
                         "timepoint": ["0"] * len(fovs), "Included": 1,
                         "nucleus_id": list(range(len(fovs)))})


def test_an_excluded_field_leaves_the_chokepoint(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.state.dataset.cache.update({"B02": _frame("B02"), "A01": _frame("A01")})
    ctx.state.dataset.field_exclusions[("B02", "2", "0")] = {"reason": "x", "at": ""}
    assert fx.apply_field_exclusions_to_cache(ctx) == []
    b02 = ctx.state.dataset.cache["B02"]
    assert list(df_included_mask(b02)) == [True, False, True]
    # A well with nothing excluded carries no column at all.
    assert fx.EXCLUDED_FIELD_COLUMN not in ctx.state.dataset.cache["A01"].columns


def test_restoring_the_field_removes_the_column(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.state.dataset.cache["B02"] = _frame("B02")
    ctx.state.dataset.field_exclusions[("B02", "2", "0")] = {"reason": "x", "at": ""}
    fx.apply_field_exclusions_to_cache(ctx)
    ctx.state.dataset.field_exclusions.clear()
    fx.apply_field_exclusions_to_cache(ctx)
    assert fx.EXCLUDED_FIELD_COLUMN not in ctx.state.dataset.cache["B02"].columns


def test_an_untrusted_well_is_never_filtered(tmp_path):
    ctx = _ctx(tmp_path, integrity={"B02": False})
    ctx.state.dataset.cache["B02"] = _frame("B02")
    ctx.state.dataset.field_exclusions[("B02", "2", "0")] = {"reason": "x", "at": ""}
    fx.apply_field_exclusions_to_cache(ctx)
    assert fx.EXCLUDED_FIELD_COLUMN not in ctx.state.dataset.cache["B02"].columns


def test_the_layer_is_one_the_chokepoint_ands():
    assert fx.EXCLUDED_FIELD_COLUMN in VERDICT_LAYERS


def test_the_projector_is_registered_whatever_the_profile():
    from review.data.cache_projectors import iter_cache_projectors
    from review.features.registry import build_feature_registry
    build_feature_registry()
    names = [p.__name__ for p in iter_cache_projectors()]
    assert "apply_field_exclusions_to_cache" in names


# ── persistence ─────────────────────────────────────────────────────────────

def test_exclusions_round_trip_and_drop_unreadable_records(caplog):
    live = {("B02", "2", "0"): {"reason": "blurred", "at": "t"}}
    records = pfx.to_records(live)
    assert pfx.from_records(records) == live
    with caplog.at_level("WARNING", logger="review"):
        assert pfx.from_records(records + [{"fov": "1"}, "junk"]) == live
    assert caplog.text.count("dropped") == 2


def test_decisions_keep_only_known_states(caplog):
    with caplog.at_level("WARNING", logger="review"):
        out = ptd.from_section({"k1": {"state": "accepted", "note": "n"},
                                "k2": {"state": "maybe"}})
    assert list(out) == ["k1"]
    assert "k2" in caplog.text


@pytest.mark.parametrize("domain", ["field_exclusions", "triage_decisions"])
def test_both_new_domains_are_curation_and_reach_the_provenance_log(domain):
    from review.persistence import provenance, reset_scopes
    curation = reset_scopes.scope_by_key("curation")
    assert domain in curation.domains
    assert domain in provenance.STORED_AT.values()
