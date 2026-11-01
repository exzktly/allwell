"""Review's rail frames and glyphs are declared on the feature descriptors.

Two hand-written rosters used to decide what the left rail showed: a dict of
glyphs (``review/core/section_icons.py``) and a tuple of frame keys the tab
switch hid on every change (``tab_controller.SIDEBAR_SECTION_FRAMES``), plus a
third list in the shell that built the frames. A new top-level page missing
from the glyph dict silently got no glyph; a frame missing from the hide-all
tuple was never hidden and stayed on screen under every later page; and a frame
nobody filled (``_sidebar_sample_frame``) sat in all three for as long as nobody
looked. Fifteen ``on_activate`` hooks then each repeated "show the main frame,
repaint the plate", and the one that forgot (Nanowells) opened with no plate.

Now each page declares ``icon`` and ``rail_sections``; the shell builds the
frames from ``FeatureRegistry.rail_sections()``, the tab switch hides them from
the same list, and ``tab_controller.show_rail_sections`` shows a page's frames
and repaints the plate before its hook runs. These tests hold that shape.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from review.core.feature_registry import (
    MAIN_RAIL_SECTION, FeatureDescriptor, FeatureRegistry, RailSection,
)
from review.features import registry
from tests._repo_scan import parse_source

REPO = Path(__file__).resolve().parents[1]

#: Pages with an ``on_activate`` hook that declare no rail frame, with why.
#: An absence and a considered exception look identical in code later, so the
#: exception is a row.
_NO_RAIL_SECTIONS = {
    "Home": "Home has no rail body; its plates are part of its own tabs "
            "(features/home/controller.sync_home_sidebar).",
    "Statistics": "shows the main frame itself, and only outside Split View, "
                  "where the other pane has the claim on the rail plate "
                  "(registry._activate_statistics).",
}


@pytest.fixture(scope="module")
def features() -> FeatureRegistry:
    return registry.build_feature_registry()


def test_every_top_level_page_names_a_glyph_the_icon_set_can_draw(features):
    from widgets.icons import has_glyph
    missing = [d.name for d in features.top_level() if not d.icon]
    unknown = [(d.name, d.icon) for d in features.top_level()
               if d.icon and not has_glyph(d.icon)]
    assert not missing, f"top-level pages with no rail glyph: {missing}"
    assert not unknown, (
        f"glyph names widgets.icons does not have (they would draw as a "
        f"warning triangle): {unknown}")


def test_every_page_with_a_hook_declares_its_rail_frames(features):
    undeclared = [d.name for d in features.all()
                  if d.on_activate is not None and not d.rail_sections
                  and d.name not in _NO_RAIL_SECTIONS]
    assert not undeclared, (
        f"pages whose tab switch would leave the rail empty: {undeclared}. "
        f"Declare rail_sections, or add a row with the reason to "
        f"_NO_RAIL_SECTIONS.")


def test_the_exemptions_still_describe_exempt_pages(features):
    for name in _NO_RAIL_SECTIONS:
        d = features.get(name)
        assert d is not None, f"{name!r} is exempted but not registered"
        assert d.on_activate is not None and not d.rail_sections, (
            f"{name!r} no longer needs its exemption")


def test_the_rail_frames_are_the_declared_ones_main_first(features):
    keys = [s.key for s in features.rail_sections()]
    assert keys == [
        "_sidebar_main_frame",
        "_heatmap_sidebar_frame",
        "_sidebar_preview_frame",
        "_sidebar_image_table_frame",
        "_sidebar_cell_browser_frame",
    ]
    stretch = {s.key: s.stretch for s in features.rail_sections()}
    assert stretch["_sidebar_cell_browser_frame"] == 0, (
        "Cell Browser's filters sit under the shared plate and must not take "
        "half the rail's slack")


def test_one_frame_declared_with_two_stretches_is_refused():
    reg = FeatureRegistry([
        FeatureDescriptor(name="A", rail_sections=(RailSection("_f", 1),)),
        FeatureDescriptor(name="B", rail_sections=(RailSection("_f", 0),)),
    ])
    with pytest.raises(ValueError, match="_f"):
        reg.rail_sections()


def test_a_registry_with_no_declarations_still_has_the_main_frame():
    assert FeatureRegistry([]).rail_sections() == [MAIN_RAIL_SECTION]


# ── the tab switch shows what a page declares ───────────────────────────────

class _Frame:
    def __init__(self) -> None:
        self.visible = False

    def setVisible(self, on: bool) -> None:
        self.visible = bool(on)


def _ctx(features):
    frames = {s.key: _Frame() for s in features.rail_sections()}
    refreshed: list[bool] = []
    ctx = SimpleNamespace(
        features=features,
        widgets=SimpleNamespace(get_or_none=frames.get),
        coordinator=SimpleNamespace(refresh_sidebar_map=lambda: refreshed.append(True)),
    )
    return ctx, frames, refreshed


@pytest.mark.parametrize("page, shown, repaints", [
    # The reported defect behind the old per-hook copies: Nanowells' hook was a
    # bare ``nw_on_activate`` and the page opened with no plate.
    ("Nanowells", {"_sidebar_main_frame"}, 1),
    ("Heat Map", {"_sidebar_main_frame", "_heatmap_sidebar_frame"}, 1),
    ("Cell Browser", {"_sidebar_main_frame", "_sidebar_cell_browser_frame"}, 1),
    ("Segmentation", {"_sidebar_preview_frame"}, 0),
    ("Image Table", {"_sidebar_image_table_frame"}, 0),
    ("Home", set(), 0),
])
def test_the_tab_switch_shows_exactly_the_page_s_frames(features, page, shown, repaints):
    from review.core.tab_controller import show_rail_sections
    ctx, frames, refreshed = _ctx(features)
    show_rail_sections(ctx, features.get(page))
    assert {k for k, f in frames.items() if f.visible} == shown
    assert len(refreshed) == repaints


def test_a_page_declaring_frames_without_a_hook_still_gets_them():
    from review.core.tab_controller import rail_descriptor
    d = FeatureDescriptor(name="X", rail_sections=(RailSection("_x"),))
    assert rail_descriptor(d) is d
    assert rail_descriptor(FeatureDescriptor(name="Y")) is None
    assert rail_descriptor(None) is None
    home_like = FeatureDescriptor(name="Z", on_activate=lambda _c: None)
    assert rail_descriptor(home_like) is home_like, "a hook with no frames shows none"


def test_a_page_with_no_descriptor_gets_the_main_frame(features):
    from review.core.tab_controller import show_rail_sections
    ctx, frames, refreshed = _ctx(features)
    show_rail_sections(ctx, None)
    assert {k for k, f in frames.items() if f.visible} == {"_sidebar_main_frame"}
    assert refreshed == [True]


def test_hooks_leave_the_rail_frames_to_the_tab_switch():
    """The fifteen copies stay gone: no activation hook names a rail frame,
    except the exempt Statistics, whose showing is conditional."""
    tree = parse_source(REPO / "review" / "features" / "registry.py")
    keys = {s.key for s in registry.build_feature_registry().rail_sections()}
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("_activate_"):
            continue
        if fn.name == "_activate_statistics":
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and node.value in keys:
                offenders.append((fn.name, node.value))
    assert not offenders, f"hooks still showing rail frames themselves: {offenders}"


# ── no page is named in core ────────────────────────────────────────────────

def test_core_rail_code_names_no_page():
    """The rail's core modules decide by declaration, never by a page's name:
    ``tab_controller`` used to show Heat Map's configurator with
    ``"Heat Map" in visible`` and set plate drag mode with
    ``tab == "Heat Map"``; ``sidebar_view`` built the configurator through a
    Heat-Map-named service. A name check is a roster of one — a second page with
    a configurator or a drag source would be silently missed."""
    import ast as _ast

    pages = {d.name for d in registry.build_feature_registry().all()}
    # tab_controller also owns restore, history and smoke checks, which name
    # pages for their own reasons; the rail is these functions of it.
    rail_fns = {"on_tab_change", "show_rail_sections", "rail_descriptor",
                "_sidebar_section_frames", "sync_rail_plate_drag_mode",
                "_rail_group_caption", "sync_section_nav_from_notebook"}
    scopes = []
    for rel in ("review/core/tab_controller.py", "review/core/sidebar_view.py",
                "review/core/app_shell_view.py"):
        tree = parse_source(REPO / rel)
        if rel.endswith("tab_controller.py"):
            fns = [n for n in tree.body if isinstance(n, _ast.FunctionDef) and n.name in rail_fns]
            assert {f.name for f in fns} == rail_fns, "a rail function was renamed"
            scopes += [(rel, f) for f in fns]
        else:
            scopes.append((rel, tree))
    offenders = []
    for rel, scope in scopes:
        for node in _ast.walk(scope):
            if isinstance(node, _ast.Constant) and node.value in pages:
                offenders.append((rel, node.lineno, node.value))
            if (isinstance(node, _ast.Constant) and isinstance(node.value, str)
                    and node.value.startswith("heatmap.")):
                offenders.append((rel, node.lineno, node.value))
    assert not offenders, f"core rail code names pages: {offenders}"


def test_heat_map_declares_its_configurator_and_its_drag_source(features):
    hm = features.get("Heat Map")
    assert hm.plate_drag_source is True
    built = [s for s in hm.rail_sections if s.build is not None]
    assert [s.key for s in built] == ["_heatmap_sidebar_frame"]
    others = [d.name for d in features.all() if d.plate_drag_source]
    assert others == ["Heat Map"]


def test_image_browser_sub_tabs_build_from_their_descriptors(features):
    """``image_browser/controller`` used to branch on the four sub-tab names;
    it calls each leaf's ``build`` now, so every leaf must declare one."""
    leaves = features.sub_leaves("Image Browser")
    assert leaves and all(features.get(n).build is not None for n in leaves), (
        [n for n in leaves if features.get(n).build is None])
    src = parse_source(REPO / "review" / "features" / "image_browser" / "controller.py")
    named = {n.value for n in ast.walk(src) if isinstance(n, ast.Constant)} & set(leaves)
    assert not named, f"the controller still branches on sub-tab names: {named}"
