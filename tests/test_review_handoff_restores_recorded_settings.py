"""Review's hand-off into Analyze restores the run's settings as the run recorded them.

*Re-run anchored wells…* (Nanowells), *Re-run wells* (QC triage) and *Finish this run* (Home) all open Analyze
through ``review.core.analyze_handoff.open_in_analyze``. It used to hand Analyze ``ctx.state.dataset.pipeline_info``
— Review's own reading of the sidecar, which lower-cases every channel token and keeps no ``execution_options`` —
so the re-run was configured with ``dapi`` for files named ``…_DAPI.tif``, matched no image in any well, and
exited cleanly with nothing computed; and it lost the CSV prefix and every setting only ``execution_options``
carries. These tests drive the app's real order: Review's loader reads the sidecar into the dataset state, the
hand-off is pressed, and the real Analyze form applies what the shell receives (``allwell.py``'s
``_open_dataset_in_analyze`` calls ``_apply_pipeline_info`` with it).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")


def _sidecar(out_dir: Path) -> None:
    """A sidecar shaped like the pipeline's: top level as ``write_pipeline_info`` writes it (the segmentation
    channel leading ``fluor_tokens``, ``phase_token`` lower-cased) and the run's argv in ``execution_options``,
    with settings that differ from the form's defaults so a lost one shows."""
    out_dir.mkdir(parents=True)
    exec_opts = {
        "filename_schema": "well:fov:timepoint:channel", "filename_sep": "_",
        "nuclear_token": "DAPI", "fluor_tokens": ["GFP", "mCherry", "BF"], "phase_token": "BF",
        "smfish_tokens": ["mCherry"], "segmentation_method": "classical_threshold",
        "csv_prefix": "myrun", "min_nucleus_area_px": 77,
    }
    info = {
        "schema": "well:fov:timepoint:channel", "separator": "_",
        "schema_fields": ["well", "fov", "timepoint", "channel"],
        "well_index": 0, "fov_index": 1, "tp_index": 2, "channel_index": 3,
        "nuclear_token": "DAPI", "fluor_tokens": ["DAPI", "GFP", "mCherry", "BF"],
        "smfish_tokens": ["mCherry"], "phase_token": "bf",
        "segmentation_method": "classical_threshold", "execution_options": exec_opts,
    }
    (out_dir / "pipeline_info.json").write_text(json.dumps(info))


def _handed_over(root: Path) -> dict:
    """What the shell receives when the hand-off is pressed on a dataset Review loaded from *root*/out."""
    from review.core.analyze_handoff import open_in_analyze
    from review.core.dataset_state import DatasetState
    from review.domain.viewer_state import read_pipeline_info

    out = root / "out"
    ds = DatasetState()
    ds.begin_load(out)
    ds.in_dir = root / "in"
    ds.pipeline_info = read_pipeline_info(out)[3]           # what Review's loader stores
    assert ds.pipeline_info["nuclear_token"] == "dapi"       # the normalised copy the hand-off must not use
    received = []
    shell = SimpleNamespace(_open_dataset_in_analyze=lambda path, info, **kw: received.append(info))
    ctx = SimpleNamespace(state=SimpleNamespace(dataset=ds),
                          window=SimpleNamespace(window=lambda: shell))
    assert open_in_analyze(ctx, force_wells=["A01"], only_wells=["A01"], incremental=False)
    assert len(received) == 1
    return received[0]


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.run_footer

    monkeypatch.setattr(analyze.run_footer, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    return t


def test_the_channel_tokens_reach_the_form_in_the_case_the_files_use(tmp_path, tab):
    _sidecar(tmp_path / "out")
    tab._apply_pipeline_info(_handed_over(tmp_path))
    rows = [(e.text(), s.isChecked(), p.isChecked()) for e, s, p, _b in tab._fluor_rows]
    assert tab._nuclear_token_edit.text() == "DAPI"
    # Exactly the run's three fluorescent channels: not the segmentation channel as a fourth row, and with the
    # smFISH and Phase marks on the channels the run had them on.
    assert rows == [("GFP", False, False), ("mCherry", True, False), ("BF", False, True)]
    assert tab._phase_token() == "BF"


def test_the_settings_only_execution_options_carries_survive_the_hand_off(tmp_path, tab):
    _sidecar(tmp_path / "out")
    tab._apply_pipeline_info(_handed_over(tmp_path))
    opts = tab._collect_run_options()
    assert opts["csv_prefix"] == "myrun"
    assert str(opts["min_nucleus_area_px"]) == "77"


def test_an_unreadable_sidecar_hands_over_nothing_rather_than_the_lossy_copy(tmp_path):
    _sidecar(tmp_path / "out")
    from review.core.analyze_handoff import recorded_settings
    from review.core.dataset_state import DatasetState

    ds = DatasetState()
    ds.begin_load(tmp_path / "out")
    ds.pipeline_info = {"nuclear_token": "dapi"}
    (tmp_path / "out" / "pipeline_info.json").write_text("{not json")
    assert recorded_settings(SimpleNamespace(state=SimpleNamespace(dataset=ds))) == {}
