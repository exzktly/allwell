"""Cell Browser re-collects when groups change while it shares a Split View.

Group edits funnel through ``rebuild_all`` (which redraws the plots) and never
emit ``selection_changed`` — so the Cell Browser gallery, which follows the same
rep-set wells the Scatter draws, used to go stale next to a live Scatter pane.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _spy_cell_browser_hook(app, monkeypatch):
    """Replace the (frozen) Cell Browser descriptor with a spy, return a counter."""
    called = {"n": 0}
    orig_get = app._features.get
    desc = orig_get("Cell Browser")
    assert desc is not None and desc.on_selection_changed is not None
    spy = SimpleNamespace(
        on_selection_changed=lambda _app: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(
        app._features, "get",
        lambda name: spy if name == "Cell Browser" else orig_get(name))
    return called


def test_group_edit_recollects_cell_browser_in_split(loaded_app, monkeypatch):
    from well_viewer.core import selection_controller as sc

    app = loaded_app
    # Pretend the Cell Browser is the live, on-screen split pane. We drive the
    # rebuild_all seam directly rather than standing up the full two-pane widget
    # tree, but use the real visibility/registry plumbing the fix relies on.
    app._split_active = True
    monkeypatch.setattr(app, "_is_tab_visible", lambda name: name == "Cell Browser")
    called = _spy_cell_browser_hook(app, monkeypatch)

    sc.rebuild_all(app)
    assert called["n"] == 1  # group edit propagated to the Cell Browser


def test_group_edit_skips_cell_browser_when_not_visible(loaded_app, monkeypatch):
    from well_viewer.core import selection_controller as sc

    app = loaded_app
    app._split_active = True
    monkeypatch.setattr(app, "_is_tab_visible", lambda name: False)
    called = _spy_cell_browser_hook(app, monkeypatch)

    sc.rebuild_all(app)
    assert called["n"] == 0  # off-screen → no needless re-collect
