"""The ``pipeline_info.json`` blocks the user owns survive a wholesale rewrite.

Two code paths replace the sidecar in full — the pipeline's own
``write_pipeline_info`` on a re-run, and the remote runner's end-of-run rsync of
the remote sidecar over the local one. Only the first carried the user's blocks
across; the second carried only ``run_report``. On a batched remote run that lost
the cell-gating thresholds the local auto-threshold pass had written moments
earlier, along with any sample definitions, ratios and notes saved in Review.

``pipeline.sidecar`` is the one definition both now share.
"""

from __future__ import annotations

import json

from pipeline.sidecar import USER_SIDE_BLOCKS, read_user_blocks, restore_user_blocks


def _write(path, obj):
    path.write_text(json.dumps(obj, indent=2))
    return path


def test_the_block_list_is_what_the_review_side_writes():
    assert set(USER_SIDE_BLOCKS) == {
        "cell_gating", "sample_definitions", "ratios", "notes"}


def test_read_returns_only_the_user_blocks(tmp_path):
    p = _write(tmp_path / "pipeline_info.json", {
        "schema": "well:channel:timepoint",          # run state, not the user's
        "cell_gating": {"thresh_frac_on": {"gfp": 12.0}},
        "notes": "keep me",
    })
    assert read_user_blocks(p) == {
        "cell_gating": {"thresh_frac_on": {"gfp": 12.0}}, "notes": "keep me"}


def test_read_tolerates_a_missing_or_broken_sidecar(tmp_path):
    assert read_user_blocks(tmp_path / "nope.json") == {}
    assert read_user_blocks(_write(tmp_path / "a.json", ["not", "a", "dict"])) == {}
    bad = tmp_path / "b.json"
    bad.write_text("{ this is not json")
    assert read_user_blocks(bad) == {}


def test_restore_puts_back_what_the_overwrite_dropped(tmp_path):
    p = tmp_path / "pipeline_info.json"
    prior = read_user_blocks(_write(p, {
        "cell_gating": {"thresh_frac_on": {"dic": 5.0, "w1594": 0.0}},
        "ratios": [{"name": "GFP/RFP"}],
    }))
    # The fetched sidecar: run state only, none of the user's blocks.
    _write(p, {"schema": "well:channel:timepoint", "run_report": {"wells": {}}})

    assert sorted(restore_user_blocks(p, prior)) == ["cell_gating", "ratios"]
    after = json.loads(p.read_text())
    assert after["cell_gating"] == {"thresh_frac_on": {"dic": 5.0, "w1594": 0.0}}
    assert after["ratios"] == [{"name": "GFP/RFP"}]
    assert after["schema"] == "well:channel:timepoint"   # run state untouched
    assert after["run_report"] == {"wells": {}}


def test_restore_never_overwrites_a_block_the_new_file_has(tmp_path):
    """A run that legitimately produced its own keeps it — the rescue is for
    blocks the overwrite dropped, not a way to pin stale values."""
    p = tmp_path / "pipeline_info.json"
    prior = {"cell_gating": {"thresh_frac_on": {"gfp": 1.0}}}
    _write(p, {"cell_gating": {"thresh_frac_on": {"gfp": 999.0}}})
    assert restore_user_blocks(p, prior) == []
    assert json.loads(p.read_text())["cell_gating"]["thresh_frac_on"]["gfp"] == 999.0


def test_restore_is_a_no_op_with_nothing_to_restore(tmp_path):
    p = _write(tmp_path / "pipeline_info.json", {"schema": "x"})
    assert restore_user_blocks(p, {}) == []
    assert restore_user_blocks(p, None) == []
    assert json.loads(p.read_text()) == {"schema": "x"}


def test_restore_cannot_take_a_run_down(tmp_path):
    """Best-effort by contract: the sidecar is a convenience and a rescue that
    raises would fail a run whose real output is already on disk."""
    missing = tmp_path / "nope.json"
    assert restore_user_blocks(missing, {"notes": "x"}) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert restore_user_blocks(bad, {"notes": "x"}) == []
