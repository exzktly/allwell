"""A ``feature_slice`` name that no feature registers is a guard that never fires.

``AppState.feature_slice(name)`` answers ``None`` for a feature that is not in
this build — which is what lets core code run in a profile that dropped a tab.
It answers ``None`` just as quietly for a name **no build ever registers**, and
then the code it guards is dead in every build, silently.

That is not hypothetical: the guard added around
``plot_toggles.toggle_fov_replicates``' call into the bar feature was first
written as ``feature_slice("barplot")``, and the slice is registered as
``"bar"``. It would have skipped the button refresh in every build, including
the ones the guard exists to leave working — a fix that reads as a fix, passes
its tests, and does nothing.

The registered names come from ``features/registry.py``'s own ``StateSpec``
entries, so adding a feature needs no edit here.
"""

from __future__ import annotations

import ast
import pathlib
import re

from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "well_viewer" / "features" / "registry.py"


def registered_slice_names() -> set[str]:
    """Every name a ``StateSpec`` attaches to ``AppState``."""
    return set(re.findall(r'StateSpec\(\s*"([a-z_]+)"',
                          REGISTRY.read_text(encoding="utf-8")))


def test_the_registry_still_declares_slices():
    """The anchor. If this comes back empty the test below passes vacuously,
    which is how a scan-based guard stops measuring anything without failing."""
    names = registered_slice_names()
    assert len(names) >= 10, (
        f"only {len(names)} slice names found in registry.py — has StateSpec "
        "been renamed? The guard below is reading nothing.")


def test_every_feature_slice_lookup_names_a_registered_slice():
    """A misspelled name makes the branch behind it unreachable in every build."""
    known = registered_slice_names()
    offenders: list[str] = []
    for path in python_sources():
        if path == REGISTRY:
            continue
        tree = parse_source(path)
        # Module-level names bound to a string literal, so a call whose argument
        # is ``_NAME`` rather than ``"bar"`` is still resolved. A first draft
        # read only ``ast.Constant`` arguments and was evaded by one
        # assignment.
        consts: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                consts[node.targets[0].id] = node.value.value
        # ``fn = ctx.state.feature_slice`` then ``fn("barplot")`` — the bound
        # method reaches the same code with no ``feature_slice`` attribute on
        # the call, so the alias is followed too.
        aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and getattr(node.value, "attr", "") == "feature_slice":
                aliases.add(node.targets[0].id)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            is_direct = getattr(node.func, "attr", "") == "feature_slice"
            is_alias = isinstance(node.func, ast.Name) and node.func.id in aliases
            if not (is_direct or is_alias) or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                name = arg.value
            elif isinstance(arg, ast.Name) and arg.id in consts:
                name = consts[arg.id]
            else:
                continue
            if name not in known:
                offenders.append(
                    f"{rel(path)}:{node.lineno}: feature_slice({name!r}) — "
                    "no feature registers that slice, so this is always None")
    assert not offenders, (
        f"{len(offenders)} lookup(s) name a slice nothing registers "
        f"(registered: {sorted(known)}):\n  " + "\n  ".join(offenders))


def test_the_owner_map_and_the_registry_agree():
    """``AppState.__getattr__`` turns a missing feature slice into a message
    naming the feature that would have supplied it — but only for names in
    ``_FEATURE_STATE_OWNERS``. A registered slice absent from that map gives a
    bare ``AttributeError`` about a private attribute instead, which is the
    error the map exists to replace; a map entry no feature registers is a
    message that can never be shown.

    Five of the thirteen slices were missing from the map.
    """
    from well_viewer.core.app_state import _FEATURE_STATE_OWNERS

    registered = registered_slice_names()
    owned = set(_FEATURE_STATE_OWNERS)
    unowned = sorted(registered - owned)
    orphaned = sorted(owned - registered)
    assert not unowned, (
        "these slices are registered by a feature but absent from "
        f"_FEATURE_STATE_OWNERS, so a build without them raises a bare "
        f"AttributeError instead of naming the feature: {unowned}")
    assert not orphaned, (
        "these _FEATURE_STATE_OWNERS entries name a slice no feature "
        f"registers, so their message can never be shown: {orphaned}")
