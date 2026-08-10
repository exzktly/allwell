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
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _spy_plot_redraws(app, monkeypatch):
    """Record which plots ``rebuild_all`` redraws. Returns (feature_names, line_calls)."""
    ctx = app._ctx
    names: list[str] = []
    orig_feat_redraw = ctx.features.redraw
    monkeypatch.setattr(
        ctx.features, "redraw",
        lambda c, name: (names.append(name), True)[1])
    line = {"n": 0}
    monkeypatch.setattr(app, "_redraw", lambda: line.__setitem__("n", line["n"] + 1))
    return names, line


def test_group_edit_skips_all_plot_redraws_when_none_visible(loaded_app, monkeypatch):
    from well_viewer.core import selection_controller as sc

    app = loaded_app
    # Home open → no plot on screen.
    monkeypatch.setattr(app, "_is_tab_visible", lambda name: False)
    names, line = _spy_plot_redraws(app, monkeypatch)

    sc.rebuild_all(app._ctx)

    assert "Bar Plots" not in names, "off-screen bar plot must not redraw on a group edit"
    assert "Scatter Plot" not in names, "off-screen scatter must not redraw on a group edit"
    assert line["n"] == 0, "off-screen line/distribution/heat-map must not redraw"


def test_group_edit_redraws_only_the_visible_plot(loaded_app, monkeypatch):
    from well_viewer.core import selection_controller as sc

    app = loaded_app
    # Bar Plots visible (e.g. in a split pane alongside Home).
    monkeypatch.setattr(app, "_is_tab_visible", lambda name: name == "Bar Plots")
    names, line = _spy_plot_redraws(app, monkeypatch)

    sc.rebuild_all(app._ctx)

    assert "Bar Plots" in names, "a visible bar plot must still redraw live on a group edit"
    assert "Scatter Plot" not in names, "hidden scatter must stay deferred"
    assert line["n"] == 0, "hidden line/distribution/heat-map must stay deferred"
