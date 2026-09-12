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


def _narrow_profile(monkeypatch, parent: str, dropped: str):
    """Remove one sub-leaf the way a real build does: through the profile.

    A sub-leaf is not removed by commenting a descriptor out. Its parent survives,
    and ``build_feature_registry`` drops both the sub-leaf descriptor *and* the
    parent's ``subtab``-tagged ``StateSpec``s by asking the profile — which is the
    half that matters here, because a slice factory imports its feature's state
    module eagerly at app construction. Pruning the descriptor alone leaves that
    factory in place and the "deleted" package gets imported anyway.
    """
    import well_viewer.runtime_app as rt
    from core.profile import Profile
    from well_viewer.features.registry import build_feature_registry as _real

    from well_viewer.features.registry import _features

    # Ask the *descriptor*, not `includes_subtab`. Under the full profile that
    # predicate short-circuits (`features is None`, then no `[subtabs]` entry) and
    # returns True for any pair of strings — including a misspelled sub-tab and a
    # `removable=False` one, which is precisely what this is meant to refuse. It
    # never reads `removable` at all.
    leaf = next((d for d in _features()
                 if d.name == dropped and d.parent == parent), None)
    assert leaf is not None, (
        f"{dropped!r} is not a sub-leaf of {parent!r} in the registry")
    assert leaf.removable, (
        f"{dropped!r} is `removable=False` — a profile cannot drop it, so a "
        "deletion test for it would be asserting something the app forbids")

    siblings = tuple(d.name for d in _features()
                     if d.parent == parent and d.name != dropped)
    narrowed = Profile(
        name="removal-probe",
        features=frozenset(d.name for d in _features() if d.group) | {parent},
        subtabs={parent: list(siblings)},
    )
    monkeypatch.setattr(rt, "build_feature_registry", lambda: _real(narrowed))
    return rt


def _exercise(app):
    """Drive the cross-feature surfaces so any lazy call site of a removed
    feature would fire: switch every top-level tab **and every sub-tab of a
    notebook page**, invalidate the stats cache, rebuild the channel selector
    (routes through set_active_image_channel).

    The sub-tab sweep is not decoration. A sibling import inside a sub-tab's
    module only fires when that sub-tab builds, so a version of this that
    stopped at the page level passed happily while deleting the Image Table
    left the Cell Browser unimportable — the exact defect these tests exist to
    catch, invisible because nothing ever opened the tab that would have hit it.
    """
    for name in list(app._notebook.pageNames()):
        app._notebook.setCurrentByName(name)
        for ns in ("image_browser", "home"):
            sub = getattr(getattr(app._ctx.runtime, ns, None), "subtabs", None)
            if sub is None:
                continue
            for i in range(sub.count()):
                sub.setCurrentIndex(i)
    app._invalidate_stats_cache()
    from well_viewer.core.channel_state_controller import update_channel_selector
    update_channel_selector(app._ctx)


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
    # Home's two removable sub-tabs. The container package stays; only the
    # sub-package goes, so these also prove Home reaches its tab bodies through
    # the registry rather than by importing them.
    ("home.ratios", "Ratios"),
    ("home.pipeline_info", "Pipeline Info"),
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


@pytest.mark.slow
@pytest.mark.parametrize("pkg, subtab", [
    ("image_browser.image_table", "Image Table"),
    ("image_browser.cell_browser", "Cell Browser"),
    ("image_browser.playground", "Playground"),
])
def test_app_tolerates_a_sub_tab_deletion(qtbot, monkeypatch, mini_dataset,
                                          pkg, subtab):
    """Image Browser's sub-tabs are separately removable, and were not.

    Each reached sideways into a sibling for a shared helper -- the frame
    loaders on Image Table, the channel-tint palette on Cell Browser -- so
    deleting one broke another, whatever ``profiles/full.toml`` claimed. Those
    helpers are in ``data/`` and ``widgets/`` now, and this is what says the
    claim is true: the package is blocked at import and the app must still boot,
    load and operate.
    """
    for m in [n for n in list(sys.modules)
              if n == f"well_viewer.features.{pkg}"
              or n.startswith(f"well_viewer.features.{pkg}.")]:
        monkeypatch.delitem(sys.modules, m, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_ImportBlocker(pkg), *sys.meta_path])

    rt = _narrow_profile(monkeypatch, "Image Browser", subtab)
    app = rt.WellViewerApp()
    qtbot.addWidget(app)
    app._load_path(mini_dataset)
    _exercise(app)
    assert app._features.get(subtab) is None


# ── a removable sub-tab's *deferred* reach into a sibling ────────────────────

def test_the_playground_builds_reference_fields_without_the_image_table():
    """Deleting one sub-tab must not disarm another's correction modes.

    `playground/worker.py` reached into `image_table.controller` for its frame
    loader. Nothing caught it: the import is function-local, so the static guard
    could not see it, and `test_app_tolerates_a_sub_tab_deletion` opens every
    sub-tab but never selects a correction mode, so it never ran the line. The
    failure surfaced only when a user picked `basic` / `empty_well` and clicked
    -- on a daemon thread, in the one interaction the tab exists for.

    Deliberately not a full-app test: the defect is an import reached from a
    pure compute function, so blocking the package and calling that function is
    the whole experiment, and it costs milliseconds instead of an app build.
    """
    import types

    blocker = _ImportBlocker("image_browser.image_table")
    purged = [n for n in list(sys.modules)
              if n.startswith("well_viewer.features.image_browser.image_table")]
    saved = {n: sys.modules[n] for n in purged}
    for n in purged:
        del sys.modules[n]
    sys.meta_path.insert(0, blocker)
    try:
        from well_viewer.features.image_browser.playground import worker

        ctx = types.SimpleNamespace(state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(pipeline_info={}, data_dir="",
                                          in_dir="", cache={}, well_paths={}),
            channel=types.SimpleNamespace(fluor_channels=[], smfish_channels=[]),
        ))
        # No frames to find, so this returns (None, None) -- the point is that it
        # returns at all rather than raising ModuleNotFoundError off-thread.
        assert worker.build_reference_fields(
            ctx, "H12", "gfp", flatfield_mode="basic",
            background_method="empty_well", max_samples=1) == (None, None)
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(saved)
