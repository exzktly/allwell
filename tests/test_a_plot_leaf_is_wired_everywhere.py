"""A new Plotting leaf is wired into every seam that has to know about it.

Adding a plot tab is not one edit. The tab is built by its own package, and
then a dozen pieces of *shared* chrome have to find it: the redraw fan-outs,
the plate's replicate-mode behaviour, the card roster the Screen/Publication
toggle walks, the band slot, the restore router. Most of those find a tab by
iterating a list somebody wrote when there were fewer tabs, and a list is
exactly the thing nobody remembers to update — so the failure is always the
same and always quiet: the control is built, it is wired, it writes its state,
and the tab it should have reached is not in the list. Nothing raises. Nothing
goes red. The user gets a control that does nothing.

Response Curves shipped missing from six of them at once, which is what this
file is for. It states the seams **against the feature registry**, so the next
leaf is covered by having been declared rather than by anyone remembering this
list exists; where a leaf is genuinely exempt, the exemption is a row here with
the reason written in it, because a bare absence and a considered exception are
indistinguishable once the code is read a month later.

Companion guards, which own one seam each and are not repeated here:
``test_plot_card_roster`` (the card roster is complete),
``test_properties_panel_keys`` (both Properties rosters),
``test_restore_reaches_every_plotting_leaf`` (the restore router),
``test_band_globals_follow_the_leaf`` (the band's globals),
``test_feature_slice_names`` (the state-slice owner map).
"""

from __future__ import annotations

import ast

import pytest

from tests._repo_scan import parse_source, python_sources

#: Leaves that are repainted by the shared ``redraw_app`` rather than by a hook
#: of their own — the three scopes ``core/scope_registry`` registers with one
#: callable. A hook here would be a second repaint of the same figure.
_REDRAWN_BY_THE_APP_FANOUT = frozenset({"Line Graphs", "Distribution", "Heat Map"})

#: Leaves with **no** registered fold-change scope, and why.
#:
#: A scope is how the coalescer reaches a tab — every cross-tab setting
#: (fold change, the error band, the replicate unit, the active Property) fans
#: out through it, and a leaf without one is invisible to all of them, so each
#: caller that wants it repainted has to name it. That is a real cost and it is
#: written here rather than left as an absence.
_NO_FOLD_CHANGE_SCOPE = {
    "Scatter Plot": (
        "Scatter scopes its own per-axis channel and property, so the band's "
        "fan-outs are not its business; the selection and group-edit paths "
        "that ARE its business name it explicitly "
        "(core/selection_controller.rebuild_all)."
    ),
}


@pytest.fixture(scope="module")
def registry():
    from well_viewer.features.registry import build_feature_registry

    return build_feature_registry()


@pytest.fixture(scope="module")
def leaves(registry):
    out = [d for d in registry.all() if d.parent == "Plotting"]
    assert len(out) >= 5, (
        "the Plotting parent declares almost no leaves — this guard would pass "
        "by measuring nothing")
    return out


def test_every_leaf_draws_and_redraws(leaves):
    """The two hooks with no substitute: something must build it and repaint it."""
    missing = {d.name: [h for h in ("build", "redraw") if getattr(d, h, None) is None]
               for d in leaves}
    missing = {name: hooks for name, hooks in missing.items() if hooks}
    assert not missing, f"a Plotting leaf declares no builder or no redraw: {missing}"


def test_every_leaf_s_redraw_is_the_shape_the_registry_calls(leaves):
    """``FeatureRegistry.redraw`` calls ``d.redraw()(ctx)`` — a *factory*.

    The test above asks only whether the field is set, and "is not None" is a
    property the wrong callable satisfies perfectly. Response Curves declared a
    ``_lazy_hook`` — a ``hook(ctx)``, one shape short of a factory — so every
    dispatch through the registry raised ``TypeError: hook() missing 1 required
    positional argument`` instead of repainting the figure, and the whole file
    stayed green. So call it, and require a callable back.
    """
    broken = {}
    for d in leaves:
        if d.redraw is None:
            continue
        try:
            made = d.redraw()
        except TypeError as exc:
            broken[d.name] = f"calling redraw() raised {exc}"
            continue
        if not callable(made):
            broken[d.name] = f"redraw() returned {made!r}, which is not callable"
    assert not broken, (
        "a Plotting leaf's redraw is not the lazy factory the registry calls "
        f"— ``d.redraw()(ctx)`` cannot reach it: {broken}")


@pytest.mark.parametrize("hook", ["on_selection_changed", "on_channel_changed"])
def test_every_leaf_is_repainted_when_what_it_draws_changes(leaves, hook):
    """Selecting different wells, or a different channel, reaches every figure.

    A leaf without the hook keeps drawing the previous answer until something
    else repaints it — a plot of the wrong data that looks exactly like a plot
    of the right data. The three leaves the shared ``redraw_app`` already covers
    are exempt, since a hook there would repaint the same figure twice.
    """
    missing = [d.name for d in leaves
               if getattr(d, hook, None) is None
               and d.name not in _REDRAWN_BY_THE_APP_FANOUT]
    assert not missing, (
        f"a Plotting leaf has no {hook} and is not covered by the redraw_app "
        f"fan-out, so it draws the previous answer until it is left and "
        f"re-entered: {missing}")


def test_every_leaf_has_a_redraw_scope_or_a_written_reason(leaves):
    """The coalescer reaches a leaf through its scope, or not at all."""
    from well_viewer.core.scope_registry import register_default_scopes
    from well_viewer.domain.fold_change_scopes import registered_scopes

    from well_viewer.features.registry import build_feature_registry

    register_default_scopes(features=build_feature_registry())
    covered = {tab for scope in registered_scopes() for tab in scope.tab_names}
    missing = [d.name for d in leaves
               if d.name not in covered and d.name not in _NO_FOLD_CHANGE_SCOPE]
    assert not missing, (
        "a Plotting leaf has no registered fold-change scope, so every "
        "cross-tab fan-out — fold change, the error band, the replicate unit, "
        "the active Property — silently skips it. Register one through the "
        "descriptor's fold_change_scope_factory, or add it to "
        f"_NO_FOLD_CHANGE_SCOPE with the reason: {missing}")


def test_the_exemptions_still_describe_real_leaves(leaves):
    """An exemption for a tab that no longer exists is a stale claim."""
    names = {d.name for d in leaves}
    stale = sorted(set(_NO_FOLD_CHANGE_SCOPE) - names) + sorted(
        _REDRAWN_BY_THE_APP_FANOUT - names)
    assert not stale, f"an exemption names a leaf the registry does not: {stale}"


def test_every_leaf_mirrors_the_plate_in_replicate_mode(leaves):
    """With groups defined, the plate paints the active set on every plot tab.

    Not on the one missing from the list: there the plate took the branch meant
    for the Image Table and painted no wells at all, and a well click fell to
    the legacy handler that knows nothing about solo wells.
    """
    from types import SimpleNamespace

    from well_viewer.core.selection_controller import _rep_mirror_tabs
    from well_viewer.features.registry import build_feature_registry

    ctx = SimpleNamespace(features=build_feature_registry())
    mirrored = _rep_mirror_tabs(ctx)
    missing = [d.name for d in leaves if d.name not in mirrored]
    assert not missing, (
        "a Plotting leaf does not mirror the replicate-mode active set, so its "
        f"plate paints no wells and its clicks take the legacy path: {missing}")


def _band_slot_titles() -> dict[str, set[str]]:
    """``{leaf title: the calls its builder makes}``, read from the tab_views.

    The title comes from the builder's own ``plotting_band_slot(ctx, "<title>")``
    call, which is the one place a leaf names itself to the shared band.
    """
    found: dict[str, set[str]] = {}
    for path in python_sources():
        if not path.name.endswith("tab_view.py"):
            continue
        for func in ast.walk(parse_source(path)):
            if not isinstance(func, ast.FunctionDef):
                continue
            title, calls = "", set()
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if not name:
                    continue
                calls.add(name)
                if name == "plotting_band_slot" and len(node.args) >= 2:
                    arg = node.args[1]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        title = title or arg.value
            if title:
                found.setdefault(title, set()).update(calls)
    return found


def test_every_leaf_builds_into_the_shared_band(leaves):
    """A leaf that builds its own control row stacks two strips over the plot."""
    titles = _band_slot_titles()
    missing = [d.name for d in leaves if d.name not in titles]
    assert not missing, (
        "a Plotting leaf's builder never calls plotting_band_slot, so its "
        f"controls are not in the page's one control band: {missing}")


def test_every_leaf_decorates_its_plot_card(leaves):
    """Without the decorators there is no hover save and no gallery record.

    They are applied by call rather than from a roster precisely so that a tab
    wires itself up — but the call still has to be made, and it is made in the
    same builder that names the band slot.
    """
    titles = _band_slot_titles()
    missing = [name for name in titles
               if "apply_plot_card_decorators" not in titles[name]]
    assert not missing, (
        "a Plotting leaf builds a band slot but never calls "
        "apply_plot_card_decorators, so its card has no hover save button and "
        f"no Saved Plots registration: {missing}")


def test_a_group_edit_repaints_the_visible_plot_scope():
    """Editing a replicate group reaches the figure the reader is looking at.

    ``rebuild_all`` only repaints what is on screen — an off-screen figure is
    redrawn by its ``on_activate`` on the way back, and editing groups happens
    on Home where no plot is visible, so redrawing them all there cost 3–5
    matplotlib renders per well click. The visibility rule is right; naming the
    tabs was not. Response Curves was not among the names, so a group edit made
    with the curves on screen left them drawn from the previous groups.

    Asserted at the boundary — which scope's redraw actually ran — rather than
    on the group state, which was always written correctly.
    """
    from dataclasses import dataclass, field

    from well_viewer.core.app_state import AppState
    from well_viewer.core.scope_registry import redraw_app_scope
    from well_viewer.domain.fold_change_scopes import (
        _REGISTERED_SCOPES, FoldChangeScope, redraw_scopes_or_defer,
        register_fold_change_scope,
    )

    @dataclass
    class _App:
        current_tab: str = "Response Curves"
        redraws: list = field(default_factory=list)
        _app_state: AppState = field(default_factory=AppState)

        def _current_centre_tab(self) -> str:
            return self.current_tab

    snapshot = dict(_REGISTERED_SCOPES)
    _REGISTERED_SCOPES.clear()
    try:
        for name, tab in (("line", "Line Graphs"), ("response", "Response Curves")):
            redraw = (redraw_app_scope if name == "line"
                      else (lambda app, _n=name: app.redraws.append(_n)))
            register_fold_change_scope(FoldChangeScope(
                name=name, ctrl_combo_attr="", baseline_combo_attr="",
                redraw=redraw, tab_names=(tab,)))
        app = _App()
        # The branch rebuild_all takes when no default-scope tab is visible.
        redraw_scopes_or_defer(app)
        assert "response" in app.redraws, (
            "the tab on screen was not repainted after a group edit, so its "
            "figure still shows the previous groups")
    finally:
        _REGISTERED_SCOPES.clear()
        _REGISTERED_SCOPES.update(snapshot)


def test_the_group_edit_path_no_longer_names_the_plot_tabs():
    """The fan-out is the coalescer, not a list of titles.

    A list here is what left Response Curves out, so the shape is guarded
    rather than only the current membership: ``rebuild_all`` may name the two
    leaves with a stated reason — Line/Distribution/Heat Map, which share one
    redraw, and Scatter, which has no scope — and nothing else.
    """
    import ast

    from tests._repo_scan import parse_source, python_sources

    path = next(p for p in python_sources()
                if p.as_posix().endswith("core/selection_controller.py"))
    func = next(n for n in ast.walk(parse_source(path))
                if isinstance(n, ast.FunctionDef) and n.name == "rebuild_all")
    named = {node.value for node in ast.walk(func)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    allowed = {"Line Graphs", "Distribution", "Heat Map", "Scatter Plot"}
    offenders = sorted(named & {"Bar Plots", "Response Curves"})
    assert not offenders, (
        "rebuild_all names a plot tab the scope coalescer already reaches — "
        "which is the roster this whole file exists to stop growing back. Only "
        f"{sorted(allowed)} may be named, each for a written reason: {offenders}")
