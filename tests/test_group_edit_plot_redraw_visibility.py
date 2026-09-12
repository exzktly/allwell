"""Group edits (``rebuild_all``) must not re-render off-screen plots.

Editing a sample group funnels through ``rebuild_all``, which used to always
redraw Bar / Line / Scatter — a synchronous matplotlib ``cla()``+draw each —
even though group editing happens on Home → Groups, where no plot
is on screen. That paid 3–5 off-screen re-renders (plus a full stats recompute
to feed them) on every well click: the "sample-group edits are ridiculously
slow" bug. ``rebuild_all`` now guards each plot redraw by tab visibility,
mirroring the Cell Browser / Spatial / Multivariate block just below it. An
on-screen plot (including a split-view pane) still redraws live; an off-screen
one defers to its ``on_activate`` hook on the next tab switch.

**`rebuild_all` reaches those plots through three different seams, and a test
that watches one of them is blind to the other two.** This file drove only
``ctx.features.redraw`` and patched only ``app._is_tab_visible``, which stopped
describing the code when the Bar redraw moved onto the fold-change scope
coalescer: the coalescer reads ``_visible_centre_tabs`` / ``_current_centre_tab``
and calls the scope's own callable, so neither the spy nor the visibility stub
reached it. The result was one test failing against correct code and one passing
against anything at all. The three seams are:

* **bar / response** — the scope coalescer (``redraw_scopes_or_defer``), whose
  visibility comes from ``_visible_centre_tabs`` / ``_current_centre_tab`` and
  whose redraw is the scope's own callable, not a registry lookup.
* **line / distribution / heat map** — ``app._redraw()``, gated on
  ``_is_tab_visible``.
* **Scatter Plot** — ``ctx.features.redraw(ctx, "Scatter Plot")``, gated on
  ``_is_tab_visible``. Named because it is the one Plotting leaf with no
  registered scope, so no coalescer fan-out reaches it.

So :func:`_show` sets every visibility accessor from one set of tab names rather
than stubbing one of them, and :func:`_spy_plot_redraws` watches all three
seams. Asserting the deferral positively — the scope is left *dirty*, which is
what its ``on_activate`` flush later reads — rather than only "it was not
drawn", because "not drawn" is equally true of a plot that was silently dropped.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _show(app, monkeypatch, *tabs: str) -> None:
    """Put exactly *tabs* on screen, for every accessor that asks.

    `rebuild_all` asks two different questions about visibility and they must
    agree, or the test describes a window that cannot exist: a tab that
    `_is_tab_visible` calls shown while `_current_centre_tab` names another one.
    That disagreement is what made the old version of this file unable to fail.
    """
    visible = frozenset(tabs)
    monkeypatch.setattr(app, "_is_tab_visible", lambda name: name in visible)
    monkeypatch.setattr(app, "_visible_centre_tabs", lambda: visible,
                        raising=False)
    monkeypatch.setattr(
        app, "_current_centre_tab",
        lambda: next(iter(visible)) if visible else None, raising=False)


def _spy_plot_redraws(app, monkeypatch):
    """Record every plot `rebuild_all` redraws, across all three seams.

    Returns ``(scopes, features, line)`` — the scope names the coalescer drew,
    the feature names routed through the registry, and a counter for
    ``app._redraw`` (line / distribution / heat map share it).
    """
    from review.domain import fold_change_scopes as fcs

    scopes: list[str] = []
    monkeypatch.setattr(fcs, "_call_redraw",
                        lambda a, scope: scopes.append(scope.name))

    features: list[str] = []
    monkeypatch.setattr(
        app._ctx.features, "redraw",
        lambda c, name: (features.append(name), True)[1])

    line = {"n": 0}
    monkeypatch.setattr(app, "_redraw", lambda: line.__setitem__("n", line["n"] + 1))
    return scopes, features, line


def _dirty(app) -> set[str]:
    """The scopes left deferred — what an ``on_activate`` flush will redraw."""
    from review.domain import fold_change_scopes as fcs
    return set(fcs._dirty_set(app))


def test_group_edit_skips_all_plot_redraws_when_none_visible(loaded_app, monkeypatch):
    """Home open → no plot on screen → nothing repaints, everything defers."""
    from review.core import selection_controller as sc

    app = loaded_app
    _show(app, monkeypatch, "Home")
    scopes, features, line = _spy_plot_redraws(app, monkeypatch)

    sc.rebuild_all(app._ctx)

    assert scopes == [], f"off-screen scopes must not redraw on a group edit: {scopes}"
    assert "Scatter Plot" not in features, "off-screen scatter must not redraw"
    assert line["n"] == 0, "off-screen line/distribution/heat-map must not redraw"
    # The positive half: every scope is left dirty, so each repaints the moment
    # its tab is next viewed. Without this the assertions above are equally
    # satisfied by a rebuild that quietly dropped the redraws altogether.
    assert {"bar", "line"} <= _dirty(app), (
        f"a deferred scope must be marked dirty, not forgotten: {_dirty(app)}")


def test_group_edit_redraws_only_the_visible_plot(loaded_app, monkeypatch):
    """Bar Plots on screen (e.g. a split pane alongside Home) → it redraws live."""
    from review.core import selection_controller as sc

    app = loaded_app
    _show(app, monkeypatch, "Bar Plots")
    scopes, features, line = _spy_plot_redraws(app, monkeypatch)

    sc.rebuild_all(app._ctx)

    assert "bar" in scopes, "a visible bar plot must still redraw live on a group edit"
    assert "bar" not in _dirty(app), "a scope just redrawn must not also be left dirty"
    assert "Scatter Plot" not in features, "hidden scatter must stay deferred"
    assert line["n"] == 0, "hidden line/distribution/heat-map must stay deferred"


def test_group_edit_redraws_a_visible_scatter_through_the_registry(loaded_app, monkeypatch):
    """Scatter is the one leaf reached by name rather than by the coalescer.

    It has no registered fold-change scope, so nothing fans out to it and
    `rebuild_all` has to ask for it explicitly. A test that only drove the
    coalescer would not notice that call disappearing.
    """
    from review.core import selection_controller as sc

    app = loaded_app
    _show(app, monkeypatch, "Scatter Plot")
    scopes, features, line = _spy_plot_redraws(app, monkeypatch)

    sc.rebuild_all(app._ctx)

    assert "Scatter Plot" in features, "a visible scatter must redraw on a group edit"
    assert "bar" not in scopes, "hidden bar must stay deferred"
    assert line["n"] == 0, "hidden line/distribution/heat-map must stay deferred"


def test_group_edit_redraws_a_visible_line_graph(loaded_app, monkeypatch):
    """Line / Distribution / Heat Map share one redraw, gated on visibility.

    The third seam. `app._redraw` is also the scope callable for those three, so
    `redraw_scopes_or_defer` is told to skip it rather than paint them twice —
    which means a test watching only the coalescer sees nothing here either.
    """
    from review.core import selection_controller as sc

    app = loaded_app
    _show(app, monkeypatch, "Line Graphs")
    # Naming Line Graphs as the centre tab also makes `rebuild_all` call
    # `_show_line_sidebar()`, which reaches into tab bodies that build lazily —
    # `cell_gating.status_label` is None until its page is first shown, and the
    # call raised `AttributeError` on one run in seven here. That is a real
    # fragility and it is not this test's subject: the redraw gating is decided
    # before it, on `_is_tab_visible`. Stubbed so the test measures the seam it
    # names rather than intermittently failing on an unrelated one.
    monkeypatch.setattr(app, "_show_line_sidebar", lambda: None, raising=False)
    scopes, features, line = _spy_plot_redraws(app, monkeypatch)

    sc.rebuild_all(app._ctx)

    assert line["n"] == 1, "a visible line graph must redraw once on a group edit"
    assert "Scatter Plot" not in features, "hidden scatter must stay deferred"
    assert "bar" not in scopes, "hidden bar must stay deferred"
