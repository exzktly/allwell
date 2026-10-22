"""The nanowell anchors set by hand go where the tools put the images (``datasets.core.nanowell_anchors_carry``).

A tool that dropped ``in/nanowell_anchors.json`` would hand back a dataset whose anchored wells come out
unaddressed again on the next run. Each tool here runs through its real entry point: Subset keeps the anchors of
what it keeps; Merge Plates renames dataset 2's wells by the remap; Concatenate renames dataset 2's timepoints by
its shift, in a new dataset and in place, and an in-place addition that fails restores dataset 1's file.
"""

from __future__ import annotations

import pytest

from util.nanowell_anchors import Anchor, anchors_path, read_anchors, set_anchor


def _anchor(root, well, tp, x=10.0, y=20.0):
    set_anchor(anchors_path(root / "in"), well, tp, (x, y))


def test_subset_keeps_the_anchors_of_the_wells_and_timepoints_it_keeps(tmp_path):
    from datasets.subset.apply import execute_plan
    from datasets.subset.subset_compute import SubsetFilter, build_subset_plan
    from tests.test_acquisition_dataset_tools import _dataset

    source, target = tmp_path / "source", tmp_path / "target"
    _dataset(source)
    for well, tp in (("A01", "0h"), ("B02", "0h"), ("B02", "24h")):
        _anchor(source, well, tp, x=len(well + tp))
    before = anchors_path(source / "in").read_bytes()
    execute_plan(build_subset_plan(source, target, SubsetFilter(
        wells=frozenset({"B02"}), timepoint_labels=frozenset({"24h"}))))
    assert read_anchors(anchors_path(target / "in")) == {("B02", "24h"): Anchor("B02", "24h", 6.0, 20.0)}
    assert anchors_path(source / "in").read_bytes() == before


def test_merge_plates_renames_dataset_2_s_anchored_wells_by_the_remap(tmp_path):
    from datasets.merge_plates.apply import execute_plan as merge
    from datasets.merge_plates.merge_plates_compute import build_plate_merge_plan
    from tests.test_acquisition_dataset_tools import _dataset

    first, second, target = tmp_path / "first", tmp_path / "second", tmp_path / "target"
    _dataset(first)
    _dataset(second)
    _anchor(first, "A01", "0h", x=1.0)
    _anchor(second, "A01", "24h", x=2.0)
    merge(build_plate_merge_plan(first, second, target, well_map={"A01": "C01", "B02": "D02"}))
    assert read_anchors(anchors_path(target / "in")) == {("A01", "0h"): Anchor("A01", "0h", 1.0, 20.0),
                                                         ("C01", "24h"): Anchor("C01", "24h", 2.0, 20.0)}


def test_concatenate_renames_dataset_2_s_anchored_timepoints_by_its_shift(tmp_path):
    from datasets.concatenate import concatenate_compute as cc
    from datasets.concatenate.apply import execute_plan
    from tests.test_concatenate_in_place import _raw

    _raw(tmp_path / "d1", timepoints=("0h", "24h"))
    _raw(tmp_path / "d2", timepoints=("0h",), tag="d2")
    _anchor(tmp_path / "d1", "A01", "24h", x=1.0)
    _anchor(tmp_path / "d2", "A01", "0h", x=2.0)
    execute_plan(cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=24.0))
    assert read_anchors(anchors_path(tmp_path / "dest" / "in")) == {
        ("A01", "24h"): Anchor("A01", "24h", 1.0, 20.0), ("A01", "48h"): Anchor("A01", "48h", 2.0, 20.0)}


@pytest.fixture
def anchored_pair(tmp_path):
    from tests.test_concatenate_in_place import _raw

    _raw(tmp_path / "d1", timepoints=("0h", "24h"))
    _raw(tmp_path / "d2", wells=("A01", "B02", "C03"), timepoints=("0h",), tag="d2")
    _anchor(tmp_path / "d1", "A01", "24h", x=1.0)
    _anchor(tmp_path / "d2", "C03", "0h", x=3.0)
    return tmp_path


def test_adding_in_place_adds_dataset_2_s_anchors_to_dataset_1_s(anchored_pair):
    from datasets.concatenate import in_place

    in_place.append_in_place(in_place.build_append_plan(anchored_pair / "d1", anchored_pair / "d2", gap_hours=24.0),
                             lambda _line: None, workers=1)
    assert read_anchors(anchors_path(anchored_pair / "d1" / "in")) == {
        ("A01", "24h"): Anchor("A01", "24h", 1.0, 20.0), ("C03", "48h"): Anchor("C03", "48h", 3.0, 20.0)}


def test_an_in_place_addition_that_fails_after_the_anchors_were_written_restores_them(anchored_pair, monkeypatch):
    from datasets.concatenate import apply
    from datasets.concatenate import concatenate_compute as cc
    from datasets.concatenate import in_place

    before = anchors_path(anchored_pair / "d1" / "in").read_bytes()
    real = apply.carry_anchors

    def then_fail(*args, **kwargs):
        real(*args, **kwargs)
        assert anchors_path(anchored_pair / "d1" / "in").read_bytes() != before     # it was rewritten
        raise ValueError("simulated: the step after the anchors failed")

    monkeypatch.setattr(apply, "carry_anchors", then_fail)
    plan = in_place.build_append_plan(anchored_pair / "d1", anchored_pair / "d2", gap_hours=24.0)
    with pytest.raises(cc.ConcatError, match="restored exactly as it was"):
        in_place.append_in_place(plan, lambda _line: None, workers=1)
    assert anchors_path(anchored_pair / "d1" / "in").read_bytes() == before


def test_two_datasets_that_anchor_one_well_at_one_timepoint_differently_are_refused(tmp_path):
    from datasets.core.nanowell_anchors_carry import AnchorSource, carry_anchors

    for name, x in (("one", 1.0), ("two", 2.0)):
        (tmp_path / name).mkdir()
        set_anchor(anchors_path(tmp_path / name), "A01", "0h", (x, 0.0))
    with pytest.raises(ValueError, match="both datasets anchor A01 at 0h"):
        carry_anchors([AnchorSource(tmp_path / "one"), AnchorSource(tmp_path / "two")], tmp_path, lambda _l: None)
