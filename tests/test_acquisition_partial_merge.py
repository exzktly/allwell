"""Two partial conversions of one acquisition, merged by Merge Plates' record merge (tracker R12), on records
the real converter wrote — not hand-built ones, whose agreement would only be the test's own."""

from __future__ import annotations

import pytest

from pipeline.czi_explode import explode_czi
from tests.test_czi_acquisition_record import _source
from util.acquisition_record import AcquisitionRecordError, merge_plate_records, read_record


def _two_conversions(tmp_path, **source):
    czi = _source(tmp_path, **source)
    explode_czi(czi, tmp_path / "one" / "in", well_subset=("A01",))
    explode_czi(czi, tmp_path / "two" / "in", well_subset=("B02",))
    return read_record(tmp_path / "one"), read_record(tmp_path / "two")


@pytest.mark.parametrize("split", [False, True], ids=["one file", "a file per well"])
def test_two_partial_conversions_of_one_acquisition_merge(tmp_path, split):
    """The clock is read over every well of the source, not the converted ones, so the two agree on each
    timepoint's start; and the files agree on the calibration. Nothing to refuse."""
    first, second = _two_conversions(tmp_path, split=split)
    merged = merge_plate_records(first, second, {})
    assert {t["well"] for t in merged["tiles"]} == {"A01", "B02"}
    assert merged["timepoints"]["0h"]["started_at"] == first["timepoints"]["0h"]["started_at"]


def test_a_source_file_that_states_no_scale_is_refused_as_a_calibration_difference(tmp_path):
    first, second = _two_conversions(tmp_path, split=True, scales=("6.5e-7", None))
    with pytest.raises(AcquisitionRecordError) as raised:
        merge_plate_records(first, second, {})
    message = str(raised.value)
    assert "disagree about the calibration of timepoint" in message
    assert "pixel_size_um: {'x': 0.65, 'y': 0.65} against {'x': None, 'y': None}" in message
    assert "Concatenate" not in message                        # not successive acquisitions: one acquisition
