"""Build-profile system: one codebase runs as any declared feature subset.

A profile names the features a build ships; ``build_feature_registry(profile)``
keeps only those, and the removability contract drops everything else. This
guards the seam that makes the thin build a *profile of the full tree* rather
than a fork — so full-app changes to surviving regions flow in for free.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.core.profile import FULL, Profile, active_profile, load_profile
from well_viewer.features.registry import build_feature_registry


def test_full_profile_admits_everything():
    reg = build_feature_registry(FULL)
    names = {d.name for d in reg.all()}
    assert {"Plotting", "Home", "Image Browser", "Bar Plots",
            "Segmentation", "Cell Gating"} <= names


def test_profile_filters_features_and_their_subleaves():
    reg = build_feature_registry(load_profile("plotting_only"))
    names = {d.name for d in reg.all()}
    # Plotting + its five plot sub-leaves survive.
    assert names == {"Plotting", "Bar Plots", "Line Graphs", "Scatter Plot",
                     "Distribution", "Heat Map"}
    # Excluded top-levels and their sub-leaves are gone.
    assert reg.get("Home") is None
    assert reg.get("Cell Gating") is None       # sub-leaf of Home
    assert reg.get("Image Browser") is None
    assert reg.get("Segmentation") is None       # sub-leaf of Image Browser


def test_thin_profile_matches_this_branch():
    reg = build_feature_registry(load_profile("thin"))
    assert reg.get("Plotting") is not None
    assert reg.get("Home") is not None
    assert reg.get("Segmentation") is not None


def test_active_profile_env(monkeypatch):
    monkeypatch.delenv("ALLWELL_PROFILE", raising=False)
    assert active_profile() is FULL
    monkeypatch.setenv("ALLWELL_PROFILE", "full")
    assert active_profile() is FULL
    monkeypatch.setenv("ALLWELL_PROFILE", "plotting_only")
    assert active_profile().name == "plotting_only"


def test_baked_marker_selects_profile_without_env(monkeypatch, tmp_path):
    """The PyInstaller build bakes `active_profile.txt` at the bundle root; with
    no env var, active_profile() must read it (the frozen-binary path)."""
    import shutil
    import well_viewer.core.profile as prof
    monkeypatch.delenv("ALLWELL_PROFILE", raising=False)
    # Mirror a frozen bundle root: the marker + a profiles/ dir under it.
    shutil.copytree(prof.profiles_dir(), tmp_path / "profiles")
    monkeypatch.setattr(prof, "_repo_root", lambda: tmp_path)
    marker = tmp_path / prof._BAKED_MARKER
    marker.write_text("thin")
    assert prof.active_profile().name == "thin"
    marker.unlink()
    assert prof.active_profile() is prof.FULL   # gone → full app


def test_subtab_filter():
    p = Profile(name="p", features=frozenset({"Image Browser"}),
                subtabs={"Image Browser": frozenset({"Segmentation"})})
    assert p.includes_subtab("Image Browser", "Segmentation") is True
    assert p.includes_subtab("Image Browser", "Image Table") is False
    # A parent the profile keeps with no subtab restriction admits any sub-tab.
    p2 = Profile(name="p2", features=frozenset({"Image Browser"}), subtabs={})
    assert p2.includes_subtab("Image Browser", "Anything") is True


@pytest.mark.slow
def test_app_boots_and_loads_under_subset_profile(qtbot, monkeypatch, mini_dataset):
    """The whole app boots, loads a dataset, and drives every tab with only the
    profile's features present — the removability contract holds under filtering,
    not just under hand-deletion."""
    monkeypatch.setenv("ALLWELL_PROFILE", "plotting_only")
    from well_viewer.runtime_app import WellViewerApp

    app = WellViewerApp()
    qtbot.addWidget(app)
    app._load_path(mini_dataset)

    assert app._notebook.pageNames() == ["Plotting"]
    assert app._app_state.feature_slice("review_image") is None
    assert app._app_state.feature_slice("bar") is not None

    # Exercise the cross-feature surfaces so any lazy call site of an excluded
    # feature would fire.
    for name in list(app._notebook.pageNames()):
        app._notebook.setCurrentByName(name)
    app._invalidate_stats_cache()
    from well_viewer.core.channel_state_controller import update_channel_selector
    update_channel_selector(app._ctx)
