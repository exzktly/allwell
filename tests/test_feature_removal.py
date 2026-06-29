"""Removability guardrails: every Review feature can be removed by commenting
its descriptor out of ``FEATURES`` (comment-out) AND by deleting its package
from disk (deletion). Both must leave the app booting, loading a dataset, and
operating with no crash.

The registry is pruned by monkeypatching ``well_viewer.runtime_app.build_feature_registry``
(runtime_app binds the name at module scope, so patching it covers both the
registry build and the state attach). Deletion is simulated with a
``sys.meta_path`` finder that raises ``ModuleNotFoundError`` for the package, so
the test fails if any code path imports it.
"""

from __future__ import annotations

import sys

import pytest


def _prune_registry(monkeypatch, descriptor_name: str):
    import well_viewer.runtime_app as rt
    from well_viewer.core.feature_registry import FeatureRegistry
    from well_viewer.features.registry import build_feature_registry as _real

    monkeypatch.setattr(
        rt, "build_feature_registry",
        lambda: FeatureRegistry(d for d in _real().all() if d.name != descriptor_name),
    )
    return rt


def _exercise(app):
    """Drive the cross-feature surfaces so any lazy call site of a removed
    feature would fire: switch every top-level tab, invalidate the stats cache,
    rebuild the channel selector (routes through set_active_image_channel)."""
    for name in list(app._notebook.pageNames()):
        app._notebook.setCurrentByName(name)
    app._invalidate_stats_cache()
    from well_viewer.core.channel_state_controller import update_channel_selector
    update_channel_selector(app)


# ── comment-out (descriptor pruned, package still importable) ────────────────

@pytest.mark.slow
def test_app_boots_loads_and_operates_without_image_browser(qtbot, monkeypatch, mini_dataset):
    rt = _prune_registry(monkeypatch, "Image Browser")
    app = rt.WellViewerApp()
    qtbot.addWidget(app)
    app._load_path(mini_dataset)
    _exercise(app)
    assert app._features.get("Image Browser") is None
    assert "Image Browser" not in app._notebook.pageNames()
    for slc in ("review_image", "preview", "image_table", "playground"):
        assert app._app_state.feature_slice(slc) is None


@pytest.mark.slow
def test_app_boots_and_plots_without_statistics(qtbot, monkeypatch, mini_dataset):
    rt = _prune_registry(monkeypatch, "Statistics")
    app = rt.WellViewerApp()
    qtbot.addWidget(app)
    app._load_path(mini_dataset)
    app._notebook.setCurrentByName("Plotting")   # bar/line redraw → stats_compute
    app._invalidate_stats_cache()
    assert app._features.get("Statistics") is None
    assert app._app_state.feature_slice("stats") is None
    # The shared group-stats cache is core, so it survives without Statistics.
    assert app._app_state.stats_cache is not None


# ── deletion (package import blocked) ────────────────────────────────────────

class _ImportBlocker:
    def __init__(self, pkg: str) -> None:
        self._prefix = f"well_viewer.features.{pkg}"

    def find_spec(self, name, path=None, target=None):
        if name == self._prefix or name.startswith(self._prefix + "."):
            raise ModuleNotFoundError(f"blocked (simulated deletion): {name}")
        return None


@pytest.mark.slow
@pytest.mark.parametrize("pkg, descriptor", [
    ("image_browser", "Image Browser"),
    ("stats", "Statistics"),
    ("scatter", "Scatter Plot"),
    ("barplot", "Bar Plots"),
])
def test_app_tolerates_feature_deletion(qtbot, monkeypatch, mini_dataset, pkg, descriptor):
    # Purge any already-imported submodules, then block re-import — so the test
    # fails if any boot/load/operate path imports the "deleted" package.
    for m in [n for n in list(sys.modules)
              if n == f"well_viewer.features.{pkg}"
              or n.startswith(f"well_viewer.features.{pkg}.")]:
        monkeypatch.delitem(sys.modules, m, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_ImportBlocker(pkg), *sys.meta_path])

    rt = _prune_registry(monkeypatch, descriptor)
    app = rt.WellViewerApp()
    qtbot.addWidget(app)
    app._load_path(mini_dataset)
    _exercise(app)
    assert app._features.get(descriptor) is None
