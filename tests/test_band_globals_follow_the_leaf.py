"""A leaf that puts its own Channel on the band hides the band's global one.

The Plotting band carries one Channel combo, one Property combo and one
ON-cutoff box for every leaf that shares them. A leaf that scopes its own pair
— the Scatter leaves, per axis; Response Curves, because its Property list has
to carry `Fraction above threshold`, which is not a column — must therefore
hide them, or the row shows two Channel dropdowns of which only one moves the
figure, above an ON-cutoff box editing whichever channel the *band* holds
rather than the one the tab is measuring.

``centre_view`` used to decide that by testing the title against a literal
tuple of the three scatter titles. Response Curves was in neither that tuple
nor any other list, so it shipped showing both pairs. The rule now reads
``ui_helpers.OWN_CHANNEL_LEAVES``, and this file holds that roster against the
builders: a leaf that adds a channel combo to its own band slot and is missing
from the roster fails here.
"""

from __future__ import annotations

import ast
import re

from tests._repo_scan import parse_source, python_sources

#: Registry keys that name a channel selector: ``_response_ch_cb``,
#: ``_scatter_ch_x_cb``, ``_scatter_agg_ch_y_cb``. Deliberately *not* matching
#: ``_chan_cb_line`` and its three siblings, which are the legacy offscreen
#: mirrors every plot tab keeps — they are hidden and never reach the band, so
#: they say nothing about which pair the user is looking at.
_CHANNEL_KEY = re.compile(r"_ch(an)?(_[xy])?_cb$")


def _string(node) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(
        node.value, str) else ""


def _registered_vars(func) -> dict[str, str]:
    """``variable name -> registry key`` for ``x = reg.register("_key", …)``.

    Built per function by reading its own Assign nodes. Deliberately not by
    tagging the shared AST: ``tests/_repo_scan`` hands every guard the same
    trees, and a scan that writes to them — even additively — makes one guard's
    behaviour depend on whether another ran first.
    """
    out: dict[str, str] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if not (isinstance(target, ast.Name) and isinstance(value, ast.Call)):
            continue
        name = getattr(value.func, "attr", None) or getattr(value.func, "id", None)
        if name == "register" and value.args:
            key = _string(value.args[0])
            if key:
                out[target.id] = key
    return out


def _leaf_channel_scopes() -> dict[str, list[str]]:
    """``{band title: [channel keys it adds to its own slot]}``.

    Read from the builders rather than from a list here, because a list here is
    the same hand-maintained roster one level up.
    """
    found: dict[str, list[str]] = {}
    for path in python_sources():
        if not path.name.endswith("tab_view.py"):
            continue
        for func in ast.walk(parse_source(path)):
            if not isinstance(func, ast.FunctionDef):
                continue
            title = ""
            scoped: list[str] = []
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(
                    node.func, "id", None)
                if name == "plotting_band_slot" and len(node.args) >= 2:
                    title = title or _string(node.args[1])
                elif name == "add_scope" and len(node.args) >= 2:
                    target = getattr(node.args[1], "id", "")
                    if target:
                        scoped.append(target)
            if not title:
                continue
            registered = _registered_vars(func)
            found.setdefault(title, []).extend(
                key for key in (registered.get(var, "") for var in scoped)
                if key and _CHANNEL_KEY.search(key))
    return found


def test_the_scan_finds_the_builders_it_measures():
    """A scan that found nothing would pass the rule below in silence."""
    scopes = _leaf_channel_scopes()
    assert "Response Curves" in scopes and "Bar Plots" in scopes
    assert scopes["Response Curves"], (
        "the Response builder registers a channel combo on its band slot; the "
        "scan should see it")
    assert not scopes["Bar Plots"], (
        "Bar Plots shares the band's channel — its offscreen mirror must not "
        "read as owning one")


def test_every_leaf_with_its_own_channel_is_in_the_roster():
    from review.core.ui_helpers import OWN_CHANNEL_LEAVES

    missing = {title: keys for title, keys in _leaf_channel_scopes().items()
               if keys and title not in OWN_CHANNEL_LEAVES}
    assert not missing, (
        "A Plotting leaf adds its own Channel selector to the band but is not "
        "in ui_helpers.OWN_CHANNEL_LEAVES, so the band's global Channel, "
        "Property and ON-cutoff stay on screen beside it — two Channel "
        "dropdowns, one of which does nothing:\n  "
        + "\n  ".join(f"{title}: {keys}" for title, keys in sorted(missing.items())))


def test_the_shared_leaves_are_not_in_the_roster():
    """The roster hides live controls, so a leaf that needs them stays out."""
    from review.core.ui_helpers import OWN_CHANNEL_LEAVES

    for title in ("Bar Plots", "Line Graphs", "Distribution", "Heat Map"):
        assert title not in OWN_CHANNEL_LEAVES
