"""Response Curves repaints on the events that change what it draws.

The tab's figure is reached by three registry hooks and by nothing else: the
generic ``redraw_app`` fan-out covers Line, Distribution and Heat Map only.
A leaf missing a hook does not fail — it draws the *previous* answer until
something else repaints it, which is a plot of the wrong data that looks
exactly like a plot of the right data.

Response shipped with two of the three. Switching the Property while it was on
screen changed nothing until the user left the tab and came back, because
``on_activate`` was the only thing repainting it.

Read from the registry rather than by constructing an app: the descriptor is
the wiring, and a hook that is absent there cannot fire however the app is
built.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources

_REGISTRY = "well_viewer/features/registry.py"
#: Every hook a plotting leaf needs to stay current, and what goes stale
#: without it.
_REQUIRED = {
    "on_activate": "the tab shows whatever it held when it was last built",
    "on_selection_changed": "selecting different wells leaves the old curves",
    "on_channel_changed": "switching Channel or Property changes nothing until "
                          "the tab is left and re-entered",
}


def _descriptor_kwargs(name: str) -> set[str]:
    """The keyword names on the ``FeatureDescriptor`` call for *name*."""
    path = next(p for p in python_sources() if p.as_posix().endswith(_REGISTRY))
    for node in ast.walk(parse_source(path)):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "FeatureDescriptor":
            continue
        for kw in node.keywords:
            if (kw.arg == "name" and isinstance(kw.value, ast.Constant)
                    and kw.value.value == name):
                return {k.arg for k in node.keywords if k.arg}
    raise AssertionError(f"no FeatureDescriptor named {name!r} in {_REGISTRY}")


def test_the_guard_can_find_the_descriptor():
    """A lookup that found nothing would pass every check below silently."""
    assert "build" in _descriptor_kwargs("Response Curves")


def test_response_curves_repaints_on_every_event_that_changes_it():
    have = _descriptor_kwargs("Response Curves")
    missing = {hook: why for hook, why in _REQUIRED.items() if hook not in have}
    assert not missing, (
        "Response Curves is missing registry hooks, so:\n"
        + "\n".join(f"  {hook} — {why}" for hook, why in sorted(missing.items()))
    )


def test_the_metric_fan_out_names_response():
    """``set_active_metric`` reaches the leaves ``redraw_app`` does not.

    A Property change is not a channel change and does not go through the
    ``on_channel_changed`` dispatch, so the hook above is not enough on its own
    — this is the other half, and the half the user actually reported.
    """
    path = next(p for p in python_sources()
                if p.as_posix().endswith("well_viewer/core/channel_state_controller.py"))
    for node in ast.walk(parse_source(path)):
        if isinstance(node, ast.FunctionDef) and node.name == "set_active_metric":
            named = {c.value for c in ast.walk(node)
                     if isinstance(c, ast.Constant) and isinstance(c.value, str)}
            assert "Response Curves" in named, (
                "set_active_metric does not redraw Response Curves, so changing "
                "Property leaves its figure showing the previous one")
            return
    raise AssertionError("set_active_metric not found — this guard measures nothing")
