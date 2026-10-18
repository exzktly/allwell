"""The Line Graphs redraw hook honours the registry's one-argument contract.

``FeatureRegistry.redraw(ctx, name)`` calls a feature's hook as ``hook(ctx)``.
That is how a saved plot repaints its tab after a restore
(``saved_plots.controller._reapply_target_tab``) and how a restored view state
repaints the tab it moved (``tab_controller.redraw_after_restore``). The Line
Graphs hook handed back ``redraw_line_plots`` itself, whose figure inputs are
required keyword arguments, so every one of those calls raised ``TypeError`` —
caught and logged at DEBUG by the saved-plots path — and the figure kept what
it was drawn with before the restore.

The app's own fan-out (``plot_orchestrator.redraw_app``) still needs to pass
those inputs itself; it resolves the same hook and calls it with keywords.
Both shapes are pinned here.
"""
from __future__ import annotations

from types import SimpleNamespace

from review.core.feature_registry import FeatureRegistry
from review.features import registry as feature_registry


def _line_graphs_hook():
    reg = FeatureRegistry(feature_registry._features())
    desc = reg.get("Line Graphs")
    assert desc is not None and desc.redraw is not None
    return reg, desc.redraw()


def test_a_redraw_by_name_repaints_line_graphs_through_the_app_fanout(monkeypatch):
    calls = []
    import review.core.plot_orchestrator as orchestrator
    monkeypatch.setattr(orchestrator, "redraw_app", lambda ctx: calls.append(ctx))
    reg, _hook = _line_graphs_hook()
    ctx = SimpleNamespace(features=reg)

    assert reg.redraw(ctx, "Line Graphs") is True
    assert calls == [ctx], (
        "a redraw of Line Graphs by name did not repaint it through redraw_app")


def test_the_fanout_still_passes_the_line_plot_its_inputs(monkeypatch):
    seen = []
    import review.features.lineplot.controller as line_controller
    monkeypatch.setattr(line_controller, "redraw_line_plots",
                        lambda ctx, **kw: seen.append(kw))
    _reg, hook = _line_graphs_hook()
    inputs = dict(apply_ax_style=object(), all_fluor_values=object(),
                  all_fluor_values_filtered=object(), warn="#c00",
                  metric_label="Mean Intensity")

    hook(object(), **inputs)
    assert seen == [inputs]
