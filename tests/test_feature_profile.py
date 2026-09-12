"""Build-profile system: one codebase runs as any declared feature subset.

A profile names the features a build ships; ``build_feature_registry(profile)``
keeps only those, and the removability contract drops everything else. This
guards the seam that makes the thin build a *profile of the full tree* rather
than a fork — so full-app changes to surviving regions flow in for free.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from core.profile import FULL, Profile, active_profile, load_profile
from review.features.registry import build_feature_registry


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
    assert reg.get("Statistics") is not None
    assert reg.get("Home") is not None
    assert reg.get("Segmentation") is not None
    # The features thin still leaves out, so that adding one is a deliberate
    # edit here rather than a silent widening of the subset.
    for dropped in ("Multivariate", "Spatial", "smFISH", "Review CSV",
                    "Saved Plots", "Batch Export"):
        assert reg.get(dropped) is None, dropped


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
    import core.profile as prof
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
    from review.runtime_app import ReviewApp

    app = ReviewApp()
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
    from review.core.channel_state_controller import update_channel_selector
    update_channel_selector(app._ctx)


@pytest.mark.slow
def test_the_thin_build_ships_statistics(qtbot, monkeypatch, mini_dataset):
    """Statistics is part of the thin build, and boots as one.

    The manifest line is only half the claim: the tab has to build under the
    subset too. Its builder used to reach outside its own package for a host
    widget — the rail's group-editor frame — and this test existed to prove that
    frame was there in a build without the features around it. The editor is now
    part of the Statistics page and reaches for nothing, which removes that
    particular way to fail and not the reason to boot: Statistics is the widest
    page in the thin profile, it builds a plate, a collapsible, a scrolling body
    and a matplotlib canvas, and a profile that dropped anything those need
    would still fail here and nowhere else.
    """
    monkeypatch.setenv("ALLWELL_PROFILE", "thin")
    from review.runtime_app import ReviewApp

    app = ReviewApp()
    qtbot.addWidget(app)
    app._load_path(mini_dataset)

    assert app._notebook.pageNames() == [
        "Home", "Plotting", "Statistics", "Image Browser",
    ]
    assert app._app_state.feature_slice("stats") is not None

    # Drive every surviving tab, so the Statistics builder and its activation
    # hook actually run rather than merely being registered.
    for name in list(app._notebook.pageNames()):
        app._notebook.setCurrentByName(name)


# ── non-removable sub-leaves ────────────────────────────────────────────────

def test_a_non_removable_subleaf_survives_a_narrowing_subtabs_list():
    """``[subtabs]`` narrows a parent's *optional* leaves, not all of them.

    A container that enumerates its sub-tabs from the registry hands the profile
    a lever over every leaf — including ones other code reaches without asking
    whether they are present. Home's Sample Map tab is the case: the three ways
    of annotating a well are layers of it, built together from one roster, so a
    manifest able to drop it would produce a build whose Overview still reports
    groups, variables and labels with no editor for any of them.
    """
    p = Profile(name="p", features=frozenset({"Home", "Image Browser"}),
                subtabs={"Home": frozenset({"Ratios"}),
                         "Image Browser": frozenset({"Image Table"})})
    reg = build_feature_registry(p)
    assert reg.get("Cell Gating") is not None, "a removable=False leaf was dropped"
    assert reg.get("Segmentation") is not None, "a removable=False leaf was dropped"

    # And the *tab*, not just the descriptor. Keeping one without the other is
    # the shape this flag was added to prevent, and it is what Image Browser did:
    # its notebook filtered a static tuple through ``includes_subtab``, which
    # never reads ``removable``, so ``Segmentation`` survived in the registry
    # while its tab was never created.
    import types

    from review.features.home.tab_view import active_subtabs
    from review.features.image_browser.tab_view import _active_subtabs

    ctx = types.SimpleNamespace(features=reg)
    home_tabs = active_subtabs(ctx)
    for name in ("Cell Gating", "Sample Map"):
        assert name in home_tabs, f"Home tab {name!r} narrowed away"
    assert "Segmentation" in _active_subtabs(ctx), "Image Browser tab narrowed away"

    # …while the optional ones still obey the allowlist, in both halves.
    assert reg.get("Cell Browser") is None
    assert reg.get("Playground") is None
    assert "Cell Browser" not in _active_subtabs(ctx)
    assert "Pipeline Info" not in home_tabs


def test_image_browser_subtabs_match_the_registry():
    """The notebook owns the *order*; the registry owns the *membership*.

    ``SUBTABS`` leads with Image Table because that tab is the primary surface
    and the one built eagerly, which declaration order does not express. That is
    a fair split only while the two agree on which tabs exist — a sub-leaf added
    to the registry and not to the tuple would be silently absent from the app,
    which is issue #14 wearing a different hat.
    """
    from review.features.image_browser.tab_view import SUBTABS

    reg = build_feature_registry(FULL)
    assert set(SUBTABS) == set(reg.sub_leaves("Image Browser"))


def test_the_flag_does_not_rescue_a_leaf_whose_parent_is_gone():
    """It survives *its parent*, not the absence of one."""
    p = Profile(name="p", features=frozenset({"Plotting"}), subtabs={})
    reg = build_feature_registry(p)
    assert reg.get("Cell Gating") is None
    assert reg.get("Segmentation") is None


def test_every_non_removable_subleaf_is_absent_from_the_strip_catalog():
    """The flag and ``profiles/full.toml`` must agree.

    A descriptor marked non-removable but carrying a catalog entry would have
    its source deleted by ``derive_profile`` while the registry insisted the
    feature was present — the two halves of the profile system disagreeing.
    """
    import tomllib
    from pathlib import Path

    catalog = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "profiles" / "full.toml")
        .read_text(encoding="utf-8"))
    strippable = {str(f.get("name")) for f in catalog.get("feature", [])}
    kept = {d.name for d in build_feature_registry(FULL).all()
            if d.parent and not d.removable}
    assert not (kept & strippable), (
        f"marked non-removable but listed in full.toml: {sorted(kept & strippable)}")


def test_home_enumerates_its_tabs_from_the_registry():
    """The tab list and the descriptor list cannot disagree.

    Home used to filter a module constant through ``includes_subtab``, which is a
    bare string check against the ``[subtabs]`` allowlist and never consults
    descriptors. Asking it about names nothing declared meant a manifest could
    drop tabs it had never heard of: the one line ARCHITECTURE's valid-names
    table sanctioned — ``[subtabs] "Home" = ["Cell Gating"]``, meaning *keep Cell
    Gating* — silently took four editors with it.
    """
    from review.features.home.tab_view import active_subtabs

    full = build_feature_registry(FULL)
    ctx = SimpleNamespace(features=full)
    assert active_subtabs(ctx) == (
        "Overview", "Cell Gating", "Sample Map", "Ratios", "Pipeline Info",
    )

    # The sanctioned manifest line keeps the two load-bearing editors and drops
    # only what is declared removable.
    narrowed = Profile(name="p", features=frozenset({"Home"}),
                       subtabs={"Home": frozenset({"Cell Gating"})})
    ctx = SimpleNamespace(features=build_feature_registry(narrowed))
    assert active_subtabs(ctx) == ("Overview", "Cell Gating", "Sample Map")


def test_every_home_tab_has_a_builder():
    """Home dispatches through the descriptor, so one without a ``build`` would
    add a tab that silently stays empty."""
    home_leaves = [d for d in build_feature_registry(FULL).all()
                   if d.parent == "Home"]
    assert home_leaves, "premise: Home declares sub-leaves"
    missing = [d.name for d in home_leaves if d.build is None]
    assert not missing, f"Home sub-leaves with no builder: {missing}"


def test_home_subtab_behaviour_is_declared_not_listed():
    """Home's per-tab facts live on the descriptors, not in name lists.

    Which tabs inset their frame used to be a module constant in the Home
    package (``_SUBTAB_MARGINS``). That is issue #14's shape in miniature -- a
    tab added to the registry and not to the constant would silently inherit
    whichever answer the constant happened to give it, with no error and nothing
    on screen saying so.

    Two siblings are gone rather than moved. ``owns_plate`` hid the rail plate on
    a sub-tab that drew one of its own, which is the two-plates hazard from the
    other side; with no rail plate on Home there is nothing to hide.
    ``reads_well_selection`` said
    whether the rail plate stayed live while a Home sub-tab showed, and Home
    keeps no rail plate: the one it had edited a group's membership, never wrote
    ``selection.selected_wells``, and is a layer of the Sample Map tab now. A
    descriptor field with no reader is the same defect one level down from a
    control that does nothing.

    The value is asserted here because it is the *user-visible* rule, and it is
    what a careless edit to the registry would change.
    """
    reg = build_feature_registry(FULL)
    subs = {n: reg.get(n) for n in reg.sub_leaves("Home")}

    inset = {n for n, d in subs.items() if d.subtab_inset}
    assert inset == {"Sample Map"}

    for field in ("reads_well_selection", "owns_plate"):
        assert not any(hasattr(d, field) for d in build_feature_registry(FULL).all()), (
            f"{field} is retired; a descriptor carrying it again means the rail "
            "plate came back to Home without the docstring above being revisited")

    # Nothing outside Home claims the inset flag: it is a sub-tab behaviour, and
    # a top-level page's plate rule is `per_well_selection`, resolved per page.
    for d in build_feature_registry(FULL).all():
        if d.parent != "Home":
            assert not d.subtab_inset, d.name
