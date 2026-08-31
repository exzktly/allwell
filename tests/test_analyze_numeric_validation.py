"""Audit X4 / X6 — analyze-tab numeric inputs are validated at the entry
boundary. A malformed radius/FFT/mesh value, a negative count, mesh_ratio=0
(downstream division-by-zero), inverted QC area bounds, or an out-of-range
solidity must be caught up front instead of silently dropped / failing deep in
the pipeline.

Every field arrives as the raw text of a form field. The QC bounds are checked
only while "Remove debris" is on and the Cellpose parameters only while the
general-Cellpose routine is selected, because those are the conditions under
which anything reads them — a value behind a control that is off cannot change
a run, so refusing to start over it would be a false refusal.
"""
from __future__ import annotations

from analyze.tab import _numeric_option_errors


def _ok_opts(**over):
    o = dict(
        tophat_radius_nir="100", tophat_radius_fluor="100",
        fft_small_nir="", fft_small_fluor="",
        mesh_ratio="3.0", mesh_sigma="",
        tf_threads="0", workers="0", remote_batch_size="0",
        qc_remove_debris=True,
        qc_min_area="", qc_max_area="", qc_min_solidity="",
    )
    o.update(over)
    return o


def test_defaults_are_valid():
    assert _numeric_option_errors(_ok_opts()) == []


def test_non_integer_radius_is_rejected():
    errs = _numeric_option_errors(_ok_opts(tophat_radius_nir="100.5"))
    assert any("Nuclear tophat radius" in e for e in errs)


def test_negative_count_rejected():
    assert _numeric_option_errors(_ok_opts(workers="-2"))


def test_mesh_ratio_zero_rejected():
    errs = _numeric_option_errors(_ok_opts(mesh_ratio="0"))
    assert any("Mesh tile ratio" in e for e in errs)


def test_mesh_ratio_blank_ok():
    assert _numeric_option_errors(_ok_opts(mesh_ratio="")) == []


def test_inverted_qc_area_bounds_rejected():
    errs = _numeric_option_errors(_ok_opts(qc_min_area="1000", qc_max_area="10"))
    assert any("minimum area" in e for e in errs)


def test_out_of_range_solidity_rejected():
    assert _numeric_option_errors(_ok_opts(qc_min_solidity="5"))
    assert _numeric_option_errors(_ok_opts(qc_min_solidity="0.5")) == []


def test_garbage_fft_rejected():
    assert _numeric_option_errors(_ok_opts(fft_small_nir="abc"))
