"""A conversion written into the folder its source file sits in can be analysed.

The CZI converter (and the OME-TIFF and ND2 converters, which share the same
destination check) accepts the folder that holds the source file as its
destination, because that folder has no ``in/`` yet. It then writes
``<folder>/in/<WELL>.zip`` beside the ``.czi`` it read. Analyze's packed-file
redirect looked at the folder, found the ``.czi``, and refused the dataset the
conversion had just written with "convert it in Datasets first" — and, before
the conversion had run, refused to *queue* the Analyze of that output at all,
because the folder already existed (holding the source) and so was asked the
packed-file question as if it were known input.

Both halves are driven through the real entry points: the converter's own
``convert`` writes the dataset, ``plan_input_layout`` / ``resolve_input_output``
judge it as a run does, and the Analyze tab is put into the state the Job
Queue's "Output of a queued job ▸" leaves it in (``set_pending_inputs``) before
its queue-time validation is asked.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tifffile")

from services.input_resolution_service import (  # noqa: E402
    GROUPED,
    PACKED_CZI,
    detect_packed_input,
    plan_input_layout,
    resolve_input_output,
)
from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi  # noqa: E402


def _plate_czi(folder):
    folder.mkdir(parents=True, exist_ok=True)
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=1)
    return write_czi(folder / "Plate7.czi", tiles, xml=plate_metadata_xml(
        scene_wells={0: "A01", 1: "B02"}, channels=["DAPI"]))


def test_a_dataset_converted_beside_its_czi_is_runnable(tmp_path):
    from datasets.czi_import.apply import convert
    from datasets.czi_import.czi_import_compute import ConversionParams

    folder = tmp_path / "exp"
    src = _plate_czi(folder)
    # Before the conversion the folder *is* a packed input, and the redirect
    # is right: there is nothing in it Analyze can read yet.
    assert plan_input_layout(folder).action == PACKED_CZI

    convert(src, folder, ConversionParams())
    assert sorted(p.name for p in (folder / "in").glob("*.zip")) == ["A01.zip", "B02.zip"]
    assert src.exists(), "the source is left where it was, beside in/"

    plan = plan_input_layout(folder)
    assert plan.action == GROUPED, plan.message
    assert resolve_input_output(folder) == (folder / "in", folder / "out")
    assert detect_packed_input(folder) is None


def test_an_empty_in_beside_a_czi_is_still_redirected(tmp_path):
    """An ``in/`` with no wells in it is a conversion that has not happened —
    the redirect must not be switched off by a bare folder."""
    folder = tmp_path / "exp"
    _plate_czi(folder)
    (folder / "in").mkdir()
    assert plan_input_layout(folder).action == PACKED_CZI


@pytest.fixture
def tab(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    import analyze.run_footer

    monkeypatch.setattr(analyze.run_footer, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    return t


def _titles(problems):
    return [p.title for p in problems]


def test_queueing_the_analyze_of_a_pending_conversion_is_not_refused(tab, tmp_path):
    folder = tmp_path / "exp"
    _plate_czi(folder)
    tab.show_input_folder(str(folder))
    # What the Job Queue does once the field holds a queued job's output.
    tab.set_pending_inputs({"opts.raw": "⧗ written by Convert CZI"})
    assert tab.has_pending_inputs()

    assert "Packed File Input" not in _titles(tab._run_problems(for_queue=True))


def test_the_same_folder_typed_by_hand_is_still_redirected(tab, tmp_path):
    """Not pending: the folder holds a CZI and nothing else, which is exactly
    what the redirect is for."""
    folder = tmp_path / "exp"
    _plate_czi(folder)
    tab.show_input_folder(str(folder))
    assert not tab.has_pending_inputs()

    assert "Packed File Input" in _titles(tab._run_problems(for_queue=True))
