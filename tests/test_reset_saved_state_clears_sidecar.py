"""The "Reset all saved state" trash-can must clear the pipeline_info sidecar too.

The action deletes ``persistence.json``, but not every piece of user-curated
state lives there: the cell-gating thresholds and the drawn scatter gates, and
the last-applied smFISH thresholds, are written into ``pipeline_info.json`` so
they travel with the dataset. Deleting the one file therefore left them in
place, and the reload that follows restored them from the sidecar — the reset
looked like it had simply skipped the gates.

These cover the two sidecar-clearing helpers the reset handler calls. Qt-free:
the widget mirror inside the gating reset is stubbed, so what's exercised is
the slice reset plus the sidecar write.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from review.core.cell_gating_state import CellGatingState


def _sidecar(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "pipeline_info.json"
    path.write_text(json.dumps(payload))
    return path


def _ctx(tmp_path: Path, *, cell_gating=None):
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(data_dir=tmp_path),
            cell_gating=cell_gating if cell_gating is not None else CellGatingState(),
        ),
        runtime=SimpleNamespace(smfish=SimpleNamespace(thresholds_saved={"cy5": 1500.0})),
    )


# ── cell gating ─────────────────────────────────────────────────────────────

@pytest.fixture()
def _no_widget_mirror(monkeypatch):
    """Stub the Cell-Gating widget mirror — it needs the built tab."""
    from review.persistence import cell_gating as mod
    monkeypatch.setattr(mod, "_mirror_gating_to_widgets",
                        lambda _ctx, **_kw: True)
    return mod


def test_clear_drops_the_gating_block_but_keeps_pipeline_keys(tmp_path, _no_widget_mirror):
    path = _sidecar(tmp_path, {
        "fluor_tokens": ["gfp", "cy5"],          # pipeline-owned, must survive
        "separator": "_",
        "cell_gating": {
            "fluor_gates": {"gfp": 50.0},
            "scatter_gates": [{"x0": 0, "y0": 0, "x1": 1, "y1": 1, "name": "Gate 1"}],
            "use_scatter_gates": True,
        },
    })
    ctx = _ctx(tmp_path)

    _no_widget_mirror.clear_from_pipeline_info(ctx)

    written = json.loads(path.read_text())
    assert "cell_gating" not in written
    assert written["fluor_tokens"] == ["gfp", "cy5"]
    assert written["separator"] == "_"


def test_clear_resets_the_live_slice_so_the_reload_cannot_rewrite_it(tmp_path, _no_widget_mirror):
    """The slice is cleared before the sidecar, so nothing in the reload can
    persist the old gates straight back."""
    _sidecar(tmp_path, {"cell_gating": {"fluor_gates": {"gfp": 50.0}}})
    cg = CellGatingState()
    cg.fluor_gates = {"gfp": 50.0}
    cg.thresh_frac_on = {"gfp": 60.0}
    cg.scatter_gates = [{"x0": 0, "y0": 0, "x1": 1, "y1": 1, "name": "Gate 1"}]
    cg.use_scatter_gates = True
    ctx = _ctx(tmp_path, cell_gating=cg)

    _no_widget_mirror.clear_from_pipeline_info(ctx)

    assert cg.fluor_gates == {}
    assert cg.thresh_frac_on == {}
    assert cg.scatter_gates == []
    assert cg.use_scatter_gates is False
    assert cg.gating_choices is None
    assert cg.thresh_choices is None


def test_clear_is_a_no_op_without_a_loaded_dataset(_no_widget_mirror):
    ctx = _ctx(Path("/nonexistent"))
    ctx.state.dataset.data_dir = None

    _no_widget_mirror.clear_from_pipeline_info(ctx)   # must not raise


# ── smFISH thresholds ───────────────────────────────────────────────────────

def test_clear_thresholds_drops_the_block_and_the_cache(tmp_path):
    from review.persistence.smfish import clear_thresholds

    path = _sidecar(tmp_path, {
        "fluor_tokens": ["cy5"],
        "smfish_thresholds": {"cy5": 1500.0},
    })
    ctx = _ctx(tmp_path)

    assert clear_thresholds(ctx) is True

    written = json.loads(path.read_text())
    assert "smfish_thresholds" not in written
    assert written["fluor_tokens"] == ["cy5"]
    assert ctx.runtime.smfish.thresholds_saved == {}


def test_clear_thresholds_reports_nothing_removed_when_absent(tmp_path):
    from review.persistence.smfish import clear_thresholds

    _sidecar(tmp_path, {"fluor_tokens": ["cy5"]})

    assert clear_thresholds(_ctx(tmp_path)) is False


def test_clear_thresholds_survives_a_missing_sidecar(tmp_path):
    from review.persistence.smfish import clear_thresholds

    assert clear_thresholds(_ctx(tmp_path)) is False
