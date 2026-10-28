"""Converting part of a timelapse, in all three packed-file converters.

The selection is a set of the source's own timepoint tokens, and the rules it
has to keep are the stride's: the index that reads a plane and the token that
names it travel together, the kept tokens are never renumbered, and a run over
part of the timelapse says so in the log. On top of those, two of its own:

* **the stride thins the selection**, counting from its first member — so
  ``t1, t2, t4`` at stride 2 keeps ``t1, t4``, where striding the source first
  and filtering after would keep ``t2, t4``;
* **the selection is part of the resume signature** (a narrower run's member
  set is a strict subset of a wider one's, and ``well_is_complete`` tests
  ``issubset``), but is written into the marker only when set, so a folder
  converted before the field existed still matches a run that leaves it empty.

The pixel tests read each member's value back and compare it with the plane
the fixture wrote at that coordinate: a plan that filtered tokens without
indices writes exactly the right names over exactly the wrong images.
"""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest
import tifffile

from pipeline.explode_common import kept_timepoints_note, timepoint_plan

TOKENS = [f"t{i}" for i in range(6)]


# ── the plan ─────────────────────────────────────────────────────────────────

def test_an_empty_selection_is_the_whole_timelapse():
    assert timepoint_plan(TOKENS, 1, ()) == list(enumerate(TOKENS))


def test_a_selection_keeps_source_indices_in_source_order():
    """Written out of order on purpose: the plan follows the acquisition."""
    assert timepoint_plan(TOKENS, 1, ("t4", "t1", "t2")) == [
        (1, "t1"), (2, "t2"), (4, "t4")]


def test_the_stride_thins_the_selection_not_the_source():
    """Stride-then-filter would keep t2 and t4 here; the selection's first
    member is the one the stride counts from."""
    assert timepoint_plan(TOKENS, 2, ("t1", "t2", "t4")) == [
        (1, "t1"), (4, "t4")]


def test_a_token_the_source_does_not_hold_is_refused_by_name():
    with pytest.raises(ValueError) as caught:
        timepoint_plan(TOKENS, 1, ("t1", "t9"))
    message = str(caught.value)
    assert "t9" in message and "Clear the selection" in message
    assert "t1," not in message.split("It has")[0], "only the missing one is named"


def test_elapsed_hour_tokens_are_matched_as_written():
    hours = ["0h", "12h", "24h", "36h"]
    assert timepoint_plan(hours, 1, ("24h",)) == [(2, "24h")]


def test_the_log_note_states_the_selection_and_the_source_count():
    assert kept_timepoints_note(1, 6, 0, TOKENS) == ""
    assert kept_timepoints_note(1, 6, 6, TOKENS) == ""
    assert kept_timepoints_note(1, 6, 2, ["t1", "t4"]) == (
        " (2 of 6 picked: t1, t4)")
    assert kept_timepoints_note(2, 6, 3, ["t1", "t4"]) == (
        " (every 2nd of 3 picked of 6: t1, t4)")
    # No selection: exactly what the stride alone always said.
    assert kept_timepoints_note(3, 6, 0, ["t0", "t3"]) == (
        " (every 3rd of 6: t0, t3)")


# ── each converter writes the right planes under the right names ─────────────

def _value(archive, member):
    with zipfile.ZipFile(archive) as z:
        return int(np.unique(tifffile.imread(io.BytesIO(z.read(member))))[0])


def _members(archive):
    with zipfile.ZipFile(archive) as z:
        return sorted(z.namelist())


def test_nd2_writes_only_the_picked_timepoints_with_their_own_planes(tmp_path):
    pytest.importorskip("nd2")
    from pipeline.nd2_explode import explode_nd2
    from tests._nd2_fixture import plate_value, write_nd2

    src = write_nd2(tmp_path / "P.nd2", n_pos=2, n_t=4, n_c=1,
                    well_names=["A01", "B01"])
    out = tmp_path / "out"
    lines: list[str] = []
    result = explode_nd2(src, out, well_source="file", experiment="E",
                         timepoint_subset=("t3", "t1"), log_fn=lines.append)

    assert result.timepoints == ["t1", "t3"]
    assert _members(out / "B01.zip") == ["E_C0_B01_f0_t1.tif",
                                         "E_C0_B01_f0_t3.tif"]
    for t in (1, 3):
        assert _value(out / "B01.zip", f"E_C0_B01_f0_t{t}.tif") == (
            plate_value(1, t, 0)), f"t{t} holds another timepoint's plane"
    assert result.images_written == 2 * 2
    assert "2 timepoint(s) (2 of 4 picked: t1, t3)" in "".join(lines)


def test_czi_writes_only_the_picked_timepoints_with_their_own_planes(tmp_path):
    from pipeline.czi_explode import explode_czi
    from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi

    src = write_czi(
        tmp_path / "Plate.czi",
        plate_tiles(wells=2, fovs=1, timepoints=5, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "C07", 1: "A02"},
                               channels=["DAPI"]))
    out = tmp_path / "out"
    result = explode_czi(src, out, experiment="Exp",
                         timepoint_subset=("t1", "t2", "t4"),
                         timepoint_stride=2)

    # Thinned within the selection: t1 and t4, not the source's t2 and t4.
    assert result.timepoints == ["t1", "t4"]
    assert _members(out / "A02.zip") == ["Exp_DAPI_A02_f0_t1.tif",
                                         "Exp_DAPI_A02_f0_t4.tif"]
    for t in (1, 4):
        # scene 1, fov 0, timepoint t, channel 0, z 0 — plate_tiles' encoding.
        expected = (((1 * 10 + 0) * 10 + t) * 10 + 0) * 10 + 0
        assert _value(out / "A02.zip", f"Exp_DAPI_A02_f0_t{t}.tif") == expected


def test_ome_single_image_reads_the_picked_frame_of_every_position(tmp_path):
    """A packed file locates a plane at ``position × n_timepoints + t`` over
    the *source's* count; position 1's t2 is frame 5, and a selection that
    shrank that count would read position 0's frame instead."""
    from pipeline.ome_explode import explode_ome_tiff

    data = np.zeros((2 * 3, 1, 6, 6), np.uint16)
    for pos in range(2):
        for t in range(3):
            data[pos * 3 + t] = pos * 100 + t
    src = tmp_path / "p.ome.tif"
    tifffile.imwrite(str(src), data, ome=True, photometric="minisblack",
                     metadata={"axes": "TCYX", "Channel": {"Name": ["DAPI"]}})
    out = tmp_path / "out"
    result = explode_ome_tiff(src, out, grid_rows=1, grid_cols=2,
                              experiment="Exp", fovs_per_well=1,
                              frame_order="time_in_pos",
                              timepoint_subset=("t2",))

    assert result.timepoints == ["t2"]
    assert _members(out / "A02.zip") == ["Exp_DAPI_A02_f0_t2.tif"]
    assert _value(out / "A02.zip", "Exp_DAPI_A02_f0_t2.tif") == 102
    assert _value(out / "A01.zip", "Exp_DAPI_A01_f0_t2.tif") == 2


def test_an_unknown_token_is_refused_before_anything_is_written(tmp_path):
    from pipeline.ome_explode import explode_ome_tiff

    src = tmp_path / "p.ome.tif"
    with tifffile.TiffWriter(str(src), ome=True) as tw:
        for _pos in range(2):
            tw.write(np.zeros((3, 1, 6, 6), np.uint16), photometric="minisblack",
                     metadata={"axes": "TCYX", "Channel": {"Name": ["DAPI"]}})
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="t7"):
        explode_ome_tiff(src, out, grid_rows=1, grid_cols=2, experiment="Exp",
                         timepoint_subset=("t7",))
    assert not out.exists() or not list(out.glob("*.zip"))


# ── the plan the page reads ──────────────────────────────────────────────────

def test_the_plan_lists_every_source_token_and_counts_the_kept_ones(tmp_path):
    pytest.importorskip("nd2")
    from datasets.nd2_import.nd2_import_compute import (
        ConversionParams, Nd2ImportError, plan_conversion, read_source)
    from tests._nd2_fixture import write_nd2

    layout = read_source(write_nd2(tmp_path / "P.nd2", n_pos=2, n_t=4, n_c=1,
                                   well_names=["A01", "B01"]))
    plan = plan_conversion(layout, ConversionParams(timepoint_subset=("t2",)))

    assert plan.source_timepoint_tokens == ["t0", "t1", "t2", "t3"]
    assert plan.timepoint_tokens == ["t2"]
    assert "1 of 4 timepoint(s) (t2)" in plan.summary()
    # Two wells × one FOV × one timepoint × one channel.
    assert plan.summary().endswith("— 2 image(s).")

    with pytest.raises(Nd2ImportError, match="t9"):
        plan_conversion(layout, ConversionParams(timepoint_subset=("t9",)))


@pytest.mark.parametrize("module", ["ome", "czi", "nd2"])
def test_nothing_ticked_is_refused_rather_than_read_as_everything(module):
    import importlib

    compute = importlib.import_module(
        f"datasets.{module}_import.{module}_import_compute")
    assert any("Tick at least one timepoint" in p
               for p in compute.RawFields(timepoints_ticked=False).problems())
    assert not any("timepoint to convert" in p
                   for p in compute.RawFields().problems())
    params = compute.RawFields(timepoint_subset=("t1",)).to_params()
    assert params.timepoint_subset == ("t1",)


# ── the resume marker ────────────────────────────────────────────────────────

def _nd2_dataset(tmp_path, **params):
    pytest.importorskip("nd2")
    from datasets.nd2_import.apply import convert
    from datasets.nd2_import.nd2_import_compute import ConversionParams
    from tests._nd2_fixture import write_nd2

    src = write_nd2(tmp_path / "P.nd2", n_pos=2, n_t=3, n_c=1,
                    well_names=["A01", "B01"])
    dest = tmp_path / "dataset"
    convert(src, dest, ConversionParams(**params),
            log=lambda _m: None, is_cancelled=lambda: False,
            progress=lambda *_a: None, well_progress=lambda *_a: None)
    return src, dest


def test_an_unset_selection_writes_no_key_so_older_markers_still_match(tmp_path):
    """A marker written before the field existed has no ``timepoint_subset``
    key. Writing the empty default would refuse every top-up of such a folder
    although nothing about its settings had changed."""
    from datasets.core.convert_marker import DONE_MARKER
    from datasets.nd2_import.nd2_import_compute import (
        ConversionParams, destination_problem)

    src, dest = _nd2_dataset(tmp_path, well_subset=("A01",))
    done = json.loads((dest / DONE_MARKER).read_text())
    assert "timepoint_subset" not in done

    assert destination_problem(dest, src,
                               ConversionParams(well_subset=("B01",))) == ""


def test_a_different_selection_into_the_same_folder_is_refused(tmp_path):
    """A well converted under ``t0, t1`` beside one converted under every
    timepoint is a ragged plate with nothing in it saying so."""
    from datasets.nd2_import.nd2_import_compute import (
        ConversionParams, destination_problem)

    src, dest = _nd2_dataset(tmp_path, well_subset=("A01",))
    problem = destination_problem(
        dest, src, ConversionParams(well_subset=("B01",),
                                    timepoint_subset=("t0", "t1")))
    assert problem, "a narrower timepoint selection topped up a wider dataset"


def test_the_same_selection_tops_up_its_own_dataset(tmp_path):
    from datasets.nd2_import.nd2_import_compute import (
        ConversionParams, destination_problem)

    src, dest = _nd2_dataset(tmp_path, well_subset=("A01",),
                             timepoint_subset=("t0", "t2"))
    assert _members(dest / "in" / "A01.zip") == ["P_C0_A01_f0_t0.tif",
                                                 "P_C0_A01_f0_t2.tif"]
    assert destination_problem(
        dest, src, ConversionParams(well_subset=("B01",),
                                    timepoint_subset=("t0", "t2"))) == ""
