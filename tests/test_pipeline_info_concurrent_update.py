"""A viewer write to pipeline_info.json must not discard the pipeline's own keys.

``pipeline_info.json`` has several writers. The pipeline owns ``schema``,
``fluor_tokens`` and ``available_timepoints`` and rewrites the file when a run
ends; Review writes the smFISH thresholds and the cell-gating block into the
same file, each by reading the whole document, replacing its own key and writing
it all back. Analyze and Review share one window, so a run can finish while the
viewer is between that read and that write — and the viewer then puts back a
copy of the document that predates the run. The user sees the new channel or
timepoint disappear from Review with nothing in the log to explain it.

The interleave is injected at the file itself: the pipeline's write lands
immediately after the viewer's first read of the sidecar. That is the seam both
writers really share, rather than an internal function of either one.
"""

from __future__ import annotations

import json
import pathlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from well_viewer.domain.gating_state import save_gating_to_pipeline_info
from well_viewer.persistence import smfish


BEFORE_THE_RUN = {
    "schema": {"well_index": 0},
    "fluor_tokens": ["gfp"],
    "available_timepoints": [0, 24],
}
AFTER_THE_RUN = {
    "schema": {"well_index": 0},
    "fluor_tokens": ["gfp", "cy5"],
    "available_timepoints": [0, 24, 48, 72],
}


@pytest.fixture()
def sidecar(tmp_path, monkeypatch):
    """Build a pipeline_info.json that the pipeline rewrites once, immediately
    after whoever is updating it has read it.

    ``sidecar(before, after)`` returns ``(data_dir, path)``. The pipeline's
    rewrite preserves the viewer-owned blocks it finds — that is what the
    pipeline actually does — and changes only the keys it owns, so the *only*
    difference between a correct run and a lost update is whose copy of those
    keys ends up on disk.
    """
    info = tmp_path / "pipeline_info.json"

    def _build(before: dict, after: dict):
        info.write_text(json.dumps(before))
        state = {"fired": False}
        real_text = pathlib.Path.read_text
        real_bytes = pathlib.Path.read_bytes

        def _run_finishes(target: Path) -> None:
            if not state["fired"] and target == info:
                state["fired"] = True
                info.write_text(json.dumps(after))

        def _read_text(self, *args, **kwargs):
            out = real_text(self, *args, **kwargs)
            _run_finishes(self)
            return out

        def _read_bytes(self, *args, **kwargs):
            out = real_bytes(self, *args, **kwargs)
            _run_finishes(self)
            return out

        monkeypatch.setattr(pathlib.Path, "read_text", _read_text)
        monkeypatch.setattr(pathlib.Path, "read_bytes", _read_bytes)
        return tmp_path, info

    return _build


def _doc(info: Path) -> dict:
    return json.loads(info.read_text())


def test_saving_an_smfish_threshold_keeps_the_run_s_channels(sidecar):
    tmp_path, info = sidecar(BEFORE_THE_RUN, AFTER_THE_RUN)
    ctx = SimpleNamespace(
        state=SimpleNamespace(dataset=SimpleNamespace(data_dir=tmp_path)),
        runtime=SimpleNamespace(smfish=SimpleNamespace(thresholds_saved={})),
    )

    smfish.save_threshold(ctx, "cy5", 1500.0)

    doc = _doc(info)
    assert doc["smfish_thresholds"] == {"cy5": 1500.0}, (
        "the threshold itself must still be saved"
    )
    assert doc["fluor_tokens"] == ["gfp", "cy5"], (
        "the run's fluor_tokens were reverted by the threshold write: "
        f"{doc['fluor_tokens']}"
    )
    assert doc["available_timepoints"] == [0, 24, 48, 72], (
        f"the run's timepoints were reverted: {doc['available_timepoints']}"
    )


def test_saving_the_gating_block_keeps_the_run_s_channels(sidecar):
    tmp_path, info = sidecar(BEFORE_THE_RUN, AFTER_THE_RUN)

    written = save_gating_to_pipeline_info(tmp_path, {"fluor_gates": {"gfp": 12.0}})

    assert written is not None, "the gating block must still be written"
    doc = _doc(info)
    assert doc["cell_gating"] == {"fluor_gates": {"gfp": 12.0}}
    assert doc["fluor_tokens"] == ["gfp", "cy5"], (
        "the run's fluor_tokens were reverted by the gating write: "
        f"{doc['fluor_tokens']}"
    )
    assert doc["available_timepoints"] == [0, 24, 48, 72]


def test_clearing_the_thresholds_keeps_the_run_s_channels(sidecar):
    # The threshold block is already there, and the run preserves it while
    # updating its own keys — as the pipeline does.
    thresholds = {"smfish_thresholds": {"cy5": 1500.0}}
    tmp_path, info = sidecar({**BEFORE_THE_RUN, **thresholds},
                             {**AFTER_THE_RUN, **thresholds})
    ctx = SimpleNamespace(
        state=SimpleNamespace(dataset=SimpleNamespace(data_dir=tmp_path)),
        runtime=SimpleNamespace(smfish=SimpleNamespace(thresholds_saved={})),
    )

    assert smfish.clear_thresholds(ctx) is True

    doc = _doc(info)
    assert "smfish_thresholds" not in doc
    assert doc["fluor_tokens"] == ["gfp", "cy5"], (
        f"the run's fluor_tokens were reverted by the clear: {doc['fluor_tokens']}"
    )
