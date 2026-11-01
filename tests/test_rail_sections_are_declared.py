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
    ("Heat Map", {"_sidebar_main_frame"}, 1),
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


def test_a_page_with_no_descriptor_gets_the_main_frame(features):
    from review.core.tab_controller import show_rail_sections
    ctx, frames, refreshed = _ctx(features)
    show_rail_sections(ctx, None)
    assert {k for k, f in frames.items() if f.visible} == {"_sidebar_main_frame"}
    assert refreshed == [True]


def test_hooks_leave_the_rail_frames_to_the_tab_switch():
    """The fifteen copies stay gone: no activation hook names a rail frame,
    except the exempt Statistics, whose showing is conditional."""
    tree = ast.parse((REPO / "review" / "features" / "registry.py").read_text())
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
