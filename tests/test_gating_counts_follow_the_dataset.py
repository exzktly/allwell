"""The "Included for analysis" line must not survive a dataset swap.

``cell_gating_refresh_included_count`` recomputes the tab's inclusion line and
funnel from the row cache. It is called when the gating tab is built, when QC
changes, and by the gating worker — and the gating worker only runs when the
dataset carries a non-default gate.

That leaves the commonest path uncovered: a dataset whose gates are all at
their defaults starts no worker, so after a plate swap the label kept the
previous plate's counts. A real number, for the wrong plate, indistinguishable
from the right one.

The assertion is on the outbound call, not on the counts it would produce: the
defect is that nothing repaints, so a test that checked the computed value
would pass against the unfixed code (CLAUDE.md section 7.4 #1).
"""

from __future__ import annotations

import types

import pytest

from review.core import load_controller


@pytest.fixture
def ctx_with_gates(monkeypatch):
    """A ctx carrying the two knobs ``_kick_off_gating_after_load`` reads."""

    def _build(*, fluor_gates, use_scatter=False, use_thresh=False):
        app = types.SimpleNamespace(
            _cell_gating_fluor_gate_edits={},
            _app_state=types.SimpleNamespace(
                cell_gating=types.SimpleNamespace(
                    fluor_gates=fluor_gates,
                    use_scatter_gates=use_scatter,
                    use_scatter_thresh=use_thresh,
                ),
            ),
        )
        # The kick-off reaches Cell Gating through the feature-service lookup,
        # so the stub carries the real registry; the spy below still catches the
        # call because the service factory imports the name when it is invoked.
        from review.features.registry import build_feature_registry
        return types.SimpleNamespace(window=app,
                                     features=build_feature_registry())

    return _build


def _spy(monkeypatch):
    """Replace both outbound calls, returning the record of what was invoked."""
    calls = []
    import review.features.home.cell_gating.controller as gating
    monkeypatch.setattr(gating, "cell_gating_start_gating_worker",
                        lambda ctx: calls.append("worker"))
    monkeypatch.setattr(gating, "cell_gating_refresh_included_count",
                        lambda ctx, **kw: calls.append("refresh"))
    return calls


def test_default_gates_still_repaint_the_inclusion_line(ctx_with_gates, monkeypatch):
    calls = _spy(monkeypatch)
    load_controller._kick_off_gating_after_load(
        ctx_with_gates(fluor_gates={"w2": 0.0})
    )
    assert calls == ["refresh"], (
        "a default-gated dataset started no worker and repainted nothing, so "
        "the tab kept the previous dataset's counts"
    )


def test_a_non_default_gate_starts_the_worker_which_repaints_itself(
    ctx_with_gates, monkeypatch
):
    calls = _spy(monkeypatch)
    load_controller._kick_off_gating_after_load(
        ctx_with_gates(fluor_gates={"w2": 3.5})
    )
    # The worker's own start path calls the refresh with pending=True; calling
    # it a second time here would flash a stale count before the pass lands.
    assert calls == ["worker"]
