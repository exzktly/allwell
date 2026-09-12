"""ThreshCountOn survives every round trip that rebuilds the gating block.

The count cutoffs live beside the intensity ones in ``pipeline_info.json``'s
``cell_gating`` block, and two code paths reconstruct that block from named
keys rather than editing it in place — the auto-threshold pass and the
saved-plot capture. A key neither of them reads is deleted on write, silently,
on a button click. That is the failure this file exists to catch; nothing else
in the suite would.
"""

from __future__ import annotations

import json

from review.domain.gating_state import (
    DEFAULT_THRESH_COUNT_ON,
    build_gating_block,
    read_gating_params,
    save_gating_to_pipeline_info,
)
from review.persistence.cell_gating import _write_gating_block_to_slice
from review.core.cell_gating_state import CellGatingState


def _sidecar(tmp_path, block=None):
    info = {"fluor_tokens": ["w3cy5"], "schema": "std"}
    if block is not None:
        info["cell_gating"] = block
    (tmp_path / "pipeline_info.json").write_text(json.dumps(info))
    return tmp_path


def test_a_set_count_cutoff_is_written():
    block = build_gating_block({}, {}, thresh_count_on={"w3cy5": 4.0})
    assert block["thresh_count_on"] == {"w3cy5": 4.0}


def test_a_default_count_cutoff_is_omitted():
    """Only non-default values reach the sidecar, same as every other key."""
    block = build_gating_block(
        {}, {}, thresh_count_on={"w3cy5": DEFAULT_THRESH_COUNT_ON})
    assert "thresh_count_on" not in block
    assert block == {}


def test_round_trips_through_the_sidecar(tmp_path):
    _sidecar(tmp_path)
    save_gating_to_pipeline_info(
        tmp_path, build_gating_block({}, {"gfp": 60.0},
                                     thresh_count_on={"w3cy5": 4.0}))
    params = read_gating_params(tmp_path)
    cg = CellGatingState()
    _write_gating_block_to_slice(cg, params)
    assert cg.thresh_count_on == {"w3cy5": 4.0}
    assert cg.thresh_frac_on == {"gfp": 60.0}


def test_a_sidecar_without_the_key_loads_clean(tmp_path):
    """Every sidecar written before counts had their own cutoff.

    Nothing to migrate: the key is simply absent, the accessor's default
    applies, and the plots that were empty start drawing.
    """
    _sidecar(tmp_path, {"thresh_frac_on": {"gfp": 60.0}})
    cg = CellGatingState()
    applied = _write_gating_block_to_slice(cg, read_gating_params(tmp_path))
    assert applied is True
    assert cg.thresh_count_on == {}


def test_a_non_numeric_count_cutoff_is_skipped(tmp_path):
    cg = CellGatingState()
    _write_gating_block_to_slice(cg, {"thresh_count_on": {"w3cy5": "banana"}})
    assert cg.thresh_count_on == {}


def test_the_auto_threshold_rebuild_preserves_count_cutoffs(tmp_path, monkeypatch):
    """The pass sweeps intensities; it must not delete the spot cutoffs.

    ``apply_auto_thresholds_to_pipeline_info`` reads the existing block, pulls
    out what it knows by name, and hands the result to ``build_gating_block``.
    Anything it does not read is dropped on write — so this pins that it reads
    this one. The Otsu sweep itself is stubbed: what is under test is the
    rebuild, not the thresholds it computes.
    """
    from review.data import auto_threshold as at

    _sidecar(tmp_path, {"thresh_frac_on": {"gfp": 60.0},
                        "thresh_count_on": {"w3cy5": 4.0}})
    monkeypatch.setattr(at, "compute_auto_thresholds",
                        lambda out_dir, **kw: {"gfp": 123.0})

    written = at.apply_auto_thresholds_to_pipeline_info(
        tmp_path, fluor_channels=["gfp"], overwrite_existing=True)
    assert written == {"gfp": 123.0}

    saved = read_gating_params(tmp_path)
    assert saved["thresh_frac_on"]["gfp"] == 123.0
    assert saved["thresh_count_on"] == {"w3cy5": 4.0}
