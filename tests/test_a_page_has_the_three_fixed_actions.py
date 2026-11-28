"""Every page that has a band ends it in Display · Style · Share.

No kind of control had a fixed place; the band's last three buttons are now
the same three on every page, from one factory
(``review/core/band_fixed_actions.finish_band``), with a disabled button where
a page has nothing for that slot.

Two halves, because neither covers the other:

* **Declared.** Every function that creates a band, or claims a slot of the
  Plotting page's shared band, also calls ``finish_band``. Stated against the
  source and against the feature registry, so a new Plotting leaf is covered
  by being declared (CLAUDE.md §3, *A new tab is not one edit*).
* **Measured.** Pages are built through their real builders and the buttons
  the row actually ends in are read off the band.
"""

from __future__ import annotations

import ast

import pytest
from review.core import band_fixed_actions as fixed
from tests import _surface_guards as scan

#: Functions that create a band and do not finish it, with the reason.
NOT_A_PAGE_BAND: dict[tuple[str, str], str] = {
    ("review/core/ui_helpers.py", "plotting_band_slot"):
        "kept: the helper that hands a Plotting leaf its slot (and builds a "
        "free-standing band for a leaf built alone); the leaf finishes the slot",
    ("review/core/centre_view.py", "build_centre._build_plotting"):
        "kept: builds the Plotting page's shared band; each leaf finishes its "
        "own slot of it, so the band itself owns none of the three",
}


def band_builders(trees) -> list[tuple[str, str, int, bool]]:
    """``(file, function, line, finishes)`` for every function that makes a
    ``ControlBand`` or claims a Plotting slot."""
    out = []
    for rel, tree in trees:
        if rel.startswith("widgets/"):
            continue

        class V(scan._Scopes):
            def _scoped(self, node):
                if not isinstance(node, ast.ClassDef):
                    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
                    own = [c for c in calls if scan._callee(c) in ("ControlBand", "plotting_band_slot")
                           and self._innermost(node, c)]
                    if own:
                        finishes = any(scan._callee(c) == "finish_band" for c in calls)
                        out.append((rel, ".".join([*self.stack, node.name]), own[0].lineno, finishes))
                super()._scoped(node)

            @staticmethod
            def _innermost(fn, call) -> bool:
                """Whether *call* is in *fn* itself rather than a function nested in it."""
                for inner in ast.walk(fn):
                    if inner is not fn and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if any(c is call for c in ast.walk(inner)):
                            return False
                return True

            visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped
        V().visit(tree)
    return out


def unfinished(trees=None, roster=None) -> list[str]:
    trees = scan.app_trees() if trees is None else trees
    roster = NOT_A_PAGE_BAND if roster is None else roster
    return [f"{f}:{line} {fn}()" for f, fn, line, done in band_builders(trees)
            if not done and (f, fn) not in roster]


def test_every_function_that_builds_a_band_finishes_it():
    found = unfinished()
    assert not found, (
        "a band is built here and never finished: call "
        "review.core.band_fixed_actions.finish_band(band) once the page has added its "
        "actions, so the row ends in Display · Style · Share:\n  " + "\n  ".join(found))


def test_the_roster_describes_only_real_band_builders():
    live = {(f, fn) for f, fn, _l, _d in band_builders(scan.app_trees())}
    gone = [f"{f} {fn}()" for (f, fn) in sorted(NOT_A_PAGE_BAND) if (f, fn) not in live]
    assert not gone, "these NOT_A_PAGE_BAND entries no longer match a band builder:\n  " + "\n  ".join(gone)


def test_every_plotting_leaf_the_registry_lists_finishes_a_slot():
    """A leaf joins this by being declared in the registry: its feature
    package must hold a function that claims a Plotting slot and finishes it."""
    from review.features.registry import build_feature_registry
    finished = {f for f, _fn, _l, done in band_builders(scan.app_trees()) if done}
    packages = {f.split("/")[2] for f in finished if f.startswith("review/features/")}
    package_of = {"Heat Map": "heatmap", "Bar Plots": "barplot", "Line Graphs": "lineplot",
                  "Response Curves": "response", "Scatter Plot": "scatter",
                  "Distribution": "distribution"}
    leaves = build_feature_registry().sub_leaves("Plotting")
    assert set(leaves) == set(package_of), "a Plotting leaf was added: name its package here"
    missing = [leaf for leaf in leaves if package_of[leaf] not in packages]
    assert not missing, f"these Plotting leaves finish no band slot: {missing}"


# ── measured ──────────────────────────────────────────────────────────────────

#: Pages built here through the registry's builder, with what each of the
#: three is on that page: ``True`` for a working button, ``False`` for the
#: disabled placeholder.
PAGES = {
    "Bar Plots": (True, True, True),
    "Line Graphs": (True, True, True),
    "Response Curves": (True, True, True),
    "Heat Map": (True, True, True),
    "Distribution": (False, True, True),
    "Multivariate": (False, True, True),
    "Spatial": (False, True, True),
    "Nanowells": (True, True, True),        # Display holds the page's one Display range row
}


@pytest.mark.parametrize("name", sorted(PAGES))
def test_the_row_ends_in_display_style_share(name, qtbot, mini_dataset):
    """Wrong implementations: a page that never calls the factory (its row
    ends in whatever it added last), a factory that appends without ordering
    (Share before Style), a missing slot left as a gap rather than a disabled
    button, or a placeholder left enabled."""
    from tests._minimal_shell import build_page
    _ctx, _window, _frame, bands = build_page(qtbot, name, mini_dataset)
    assert bands, f"{name} built no band"
    band = bands[0]
    # A leaf of a shared band owns a slot of it; any other page owns its band.
    target = band.slot(band.slot_names()[0]) if band.slot_names() else band
    three = fixed.fixed_actions(target)
    assert [fixed.role_of(b) for b in three] == ["display", "style", "share"]
    assert [b.text().replace("▾", "").strip() for b in three] == ["Display", "Style", "Share"]
    row = band.actions()
    last_shown = [w for w in row if not w.isHidden()][-3:]
    assert last_shown == three, "the row does not end in the three fixed buttons"
    assert tuple(not b.property(fixed.PLACEHOLDER_PROPERTY) for b in three) == PAGES[name]
    assert tuple(b.isEnabled() for b in three) == PAGES[name], "a placeholder must be disabled"
