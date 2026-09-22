"""Converting part of a plate, in all three packed-file converters.

The rule the whole feature turns on: a subset filters the well **indices** a
run walks and never the mapping. A well's index is what maps it back to its
source — well ``i`` takes stage positions ``i * fovs`` onward in every
converter — so filtering the label list would renumber every well after the
first one dropped, and converting B01 alone would read A01's images and write
them under B01's name. A complete, correctly named archive of the wrong well
is the failure CLAUDE.md §7 is written against, so the first test here
compares a subset run's bytes against a whole-plate run's rather than merely
counting what came out.
"""

from __future__ import annotations

import io
import zipfile

import numpy as np
import pytest
import tifffile

from pipeline.explode_common import subset_note, wells_to_convert

pytest.importorskip("nd2")

from pipeline.nd2_explode import explode_nd2  # noqa: E402
from tests._nd2_fixture import write_nd2  # noqa: E402


def _plate(tmp_path, name="P.nd2"):
    """Three wells of two fields, each plane carrying its own coordinates."""
    return write_nd2(tmp_path / name, n_pos=6, n_t=1, n_c=1,
                     well_names=["A01", "A01", "B01", "B01", "C01", "C01"])


def _value(archive, member):
    with zipfile.ZipFile(archive) as z:
        return int(np.unique(tifffile.imread(io.BytesIO(z.read(member))))[0])


# ── the index rule ───────────────────────────────────────────────────────────

def test_the_helper_returns_indices_so_the_mapping_cannot_shift():
    labels = ["A01", "A02", "B01", "B02"]

    assert wells_to_convert(labels, ()) == [0, 1, 2, 3]
    assert wells_to_convert(labels, ("B01",)) == [2]
    # Case and zero-padding are the user's, not the plate's.
    assert wells_to_convert(labels, ("b1", "A02")) == [1, 2]


def test_a_well_the_source_does_not_hold_is_refused_by_name():
    """Rather than quietly converting fewer wells than asked for, which looks
    exactly like having asked for fewer."""
    with pytest.raises(ValueError) as caught:
        wells_to_convert(["A01", "A02"], ("H12",))

    assert "H12" in str(caught.value)


@pytest.mark.parametrize("subset", [("B01",), ("C01",), ("A01", "C01")])
def test_a_converted_well_is_byte_identical_to_a_whole_plate_run(tmp_path,
                                                                 subset):
    """The assertion the whole design exists to make true.

    Asserted on the **pixels**, not on the archive names: a run that shifted
    its mapping by one well would produce exactly the right filenames holding
    exactly the wrong images, and every count would agree.
    """
    src = _plate(tmp_path)
    whole = tmp_path / "whole"
    whole.mkdir()
    explode_nd2(src, whole, well_source="file", experiment="E")

    part = tmp_path / "part"
    part.mkdir()
    explode_nd2(src, part, well_source="file", experiment="E",
                well_subset=subset)

    assert sorted(p.stem for p in part.glob("*.zip")) == sorted(subset)
    for well in subset:
        member = f"E_C0_{well}_f0_t0.tif"
        assert _value(part / f"{well}.zip", member) == _value(
            whole / f"{well}.zip", member), f"{well} holds another well's pixels"


def test_the_wells_left_out_are_not_written_at_all(tmp_path):
    src = _plate(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()

    result = explode_nd2(src, dest, well_source="file", experiment="E",
                         well_subset=("B01",))

    assert sorted(p.stem for p in dest.glob("*.zip")) == ["B01"]
    # 1 well x 2 FOVs x 1 timepoint x 1 channel.
    assert result.images_written == 2


# ── saying so ────────────────────────────────────────────────────────────────

def test_the_run_log_states_both_numbers_for_a_subset():
    """A subset run writes a complete, correct dataset holding a fraction of
    the acquisition, and a plain well count reads identically to a plate that
    only ever had that many wells."""
    assert subset_note(96, 96) == "96 well(s)"
    assert subset_note(3, 96) == "3 of 96 well(s) (subset)"


def test_an_empty_subset_is_the_whole_plate_and_says_nothing_extra(tmp_path):
    """The default, and what every run did before subsets existed."""
    src = _plate(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()
    lines: list[str] = []

    explode_nd2(src, dest, well_source="file", experiment="E",
                well_subset=(), log_fn=lines.append)

    assert sorted(p.stem for p in dest.glob("*.zip")) == ["A01", "B01", "C01"]
    assert "subset" not in "".join(lines)


# ── converting the rest later, into the same folder ──────────────────────────

def _convert(src, dest, **params):
    from datasets.nd2_import.apply import convert
    from datasets.nd2_import.nd2_import_compute import ConversionParams

    return convert(src, dest, ConversionParams(**params),
                   log=lambda _m: None, is_cancelled=lambda: False,
                   progress=lambda *_a: None, well_progress=lambda *_a: None)


def test_the_remaining_wells_top_up_the_same_dataset(tmp_path):
    """Convert six wells today and the rest next week: one dataset.

    The workflow the setting exists for, and the reason ``well_subset`` is not
    part of the resume signature. It is instead permitted by the marker a
    completed run leaves, which records the mapping that built the folder.
    """
    from datasets.nd2_import.nd2_import_compute import destination_problem

    src = _plate(tmp_path)
    dest = tmp_path / "dataset"

    _convert(src, dest, well_subset=("A01",))
    assert sorted(p.stem for p in (dest / "in").glob("*.zip")) == ["A01"]

    # The second sitting is not refused as a stranger's plate…
    assert destination_problem(dest, src, _params(well_subset=("B01", "C01"))) == ""
    _convert(src, dest, well_subset=("B01", "C01"))

    assert sorted(p.stem for p in (dest / "in").glob("*.zip")) == [
        "A01", "B01", "C01"]


def _params(**kw):
    from datasets.nd2_import.nd2_import_compute import ConversionParams

    return ConversionParams(**kw)


def test_a_finished_subset_run_is_not_reported_as_resumable(tmp_path):
    """Topping up is not the same question as finishing an interrupted run.

    A completed run leaves a record of its mapping so the folder can be added
    to, and that record must not make the conversion look like it died
    part-way — the two markers answer different questions and the page says
    different things about them.
    """
    from datasets.nd2_import.nd2_import_compute import resume_state

    src = _plate(tmp_path)
    dest = tmp_path / "dataset"
    _convert(src, dest, well_subset=("A01",))

    assert resume_state(dest, src, _params(well_subset=("B01",))).resumable is False


@pytest.mark.parametrize("field,value", [
    ("experiment", "Other"),
    ("timepoint_stride", 2),
    ("origin", "B03"),
    ("well_source", "grid"),
])
def test_a_different_mapping_into_the_same_folder_is_still_refused(
        tmp_path, field, value):
    """The narrowing must not become a hole.

    Accepting a top-up is accepting *this* dataset's own settings; anything
    that changes which image lands under which filename is still two plates in
    one folder with nothing saying so.
    """
    from datasets.nd2_import.nd2_import_compute import destination_problem

    src = _plate(tmp_path)
    dest = tmp_path / "dataset"
    _convert(src, dest, well_subset=("A01",))

    problem = destination_problem(dest, src, _params(**{field: value}))

    assert problem, f"changing {field} was accepted into a finished dataset"


def test_a_stranger_s_plate_in_the_folder_is_refused(tmp_path):
    """A folder holding archives this converter did not write has no marker,
    so there is nothing to match and the original refusal stands."""
    from datasets.nd2_import.nd2_import_compute import destination_problem

    src = _plate(tmp_path)
    dest = tmp_path / "somebody_elses"
    (dest / "in").mkdir(parents=True)
    (dest / "in" / "A01.zip").write_bytes(b"")

    assert "Already holds converted wells" in destination_problem(
        dest, src, _params(well_subset=("B01",)))
