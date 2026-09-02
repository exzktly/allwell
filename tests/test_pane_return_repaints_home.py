"""Returning to the Review pane brings back a record written in another pane.

The path a user takes: Home says the dataset has no completeness record and
offers **Check it now**, which switches to the Datasets pane; Validate & Repair
writes a ``run_check`` block into the sidecar; the user switches back. Home is
still the current page across that round trip, so no tab changed and no
activation hook fired — the pane handler is the only thing that can notice.

``tests/test_home_page.py`` drives the real pane stack against a real window for
the end of it. What is measured here is the decision the handler makes: repaint
when the record changed, and not otherwise, because a mode toggle happens
constantly and almost never brings a new record with it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


class _Features:
    """The registry as this call site uses it: one key, and did it fire."""

    def __init__(self):
        self.calls: list[str] = []

    def call(self, key, *args, **kwargs) -> bool:
        self.calls.append(key)
        return True


def _ctx(tmp_path, info):
    return SimpleNamespace(
        state=SimpleNamespace(dataset=SimpleNamespace(
            data_dir=tmp_path, pipeline_info=info)),
        features=_Features(),
    )


def _sidecar(tmp_path, **blocks) -> None:
    (tmp_path / "pipeline_info.json").write_text(json.dumps(blocks))


def _block(finished_at: str) -> dict:
    return {"version": 1, "skipped": False, "partial_scan": False,
            "source": "validate", "finished_at": finished_at,
            "wells": {}, "totals": {}, "errors": {}, "findings": []}


@pytest.fixture
def on_return():
    pytest.importorskip("PySide6")
    from well_viewer.core.tab_controller import _resync_record_on_return
    return _resync_record_on_return


def test_a_record_written_in_the_other_pane_is_read_and_repainted(tmp_path, on_return):
    info = {"schema_fields": ["well"]}
    ctx = _ctx(tmp_path, info)
    _sidecar(tmp_path, schema_fields=["well"],
             run_check=_block("2026-08-09T12:00:00Z"))

    on_return(ctx)

    assert info["run_check"]["finished_at"] == "2026-08-09T12:00:00Z"
    assert ctx.features.calls == ["home.refresh"]


def test_a_mode_toggle_that_changed_nothing_repaints_nothing(tmp_path, on_return):
    block = _block("2026-08-09T12:00:00Z")
    ctx = _ctx(tmp_path, {"run_check": block})
    _sidecar(tmp_path, run_check=block)

    on_return(ctx)

    assert ctx.features.calls == [], (
        "the record on disk is the one already loaded — there is nothing to redraw")


def test_a_dataset_with_no_sidecar_at_all_is_not_an_error(tmp_path, on_return):
    ctx = _ctx(tmp_path, {})
    on_return(ctx)
    assert ctx.features.calls == []
