"""Ratchet: image access goes through ``ImageRepository`` (roadmap B5).

Three modules make up the image path — discovery
(``image_discovery.find_well_images_and_masks``), decode
(``image_discovery.open_imgref_as_array``) and the two cache invalidations that
belong to a dataset swap — and a feature that wanted a picture used to import
whichever of them it needed. The discovery call in particular carried four
fields off ``ctx.state.dataset`` that every caller had to assemble identically.

``data/image_repository.py`` is the one door now, and this counts what still
walks past it: an import or call of those functions from outside the data layer.
It only ratchets down.

**Two things it is deliberately not.** It does not guard
``frame_load.load_frame_array`` / ``load_mask_array``, which are the grid
features' own composed API and take the caller's inventory cache explicitly —
see the repository's module docstring for why those stay where they are. And it
is not a claim that the caches are reachable from outside: they are not, which
is what ``test_image_cache_encapsulation`` below pins separately.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._repo_scan import parse_source, python_sources, rel

ROOT = Path(__file__).resolve().parent.parent / "well_viewer"

#: The names that must be reached through the facade rather than imported.
_FACADE_ONLY = frozenset({
    "find_well_images_and_masks",
    "open_imgref_as_array",
    "invalidate_decode_cache",
})

#: The data layer owns these — it is where the facade and the implementations
#: both live, so an import between them is not a bypass.
_OWNER_DIR = ROOT / "data"

# Every feature call site was converted to ``ctx.state.images`` (or the static
# ``ImageRepository.array`` where the caller has no context, as in the
# Playground's worker thread), so the budget is 0; ratchet DOWN only.
IMAGE_REPO_BYPASS_BUDGET = 0


def _facade_only_imports(tree: ast.AST) -> list[tuple[int, str]]:
    """``(lineno, name)`` for each facade-only name imported in *tree*."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not (node.module or "").startswith("well_viewer.data"):
            continue
        for alias in node.names:
            if alias.name in _FACADE_ONLY:
                found.append((node.lineno, alias.name))
    return found


def _bypasses() -> list[str]:
    """``file:line`` for each import of a facade-only name outside ``data/``."""
    found: list[str] = []
    for path in python_sources():
        if _OWNER_DIR in path.parents:
            continue
        try:
            tree = parse_source(path)
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for lineno, name in _facade_only_imports(tree):
            found.append(f"{rel(path)}:{lineno} ({name})")
    return sorted(found)


def test_image_access_goes_through_the_repository():
    found = _bypasses()
    assert len(found) <= IMAGE_REPO_BYPASS_BUDGET, (
        f"{len(found)} site(s) reach image discovery/decode directly instead of "
        f"through ``ctx.state.images`` (or ``ImageRepository`` where there is no "
        f"context), against a budget of {IMAGE_REPO_BYPASS_BUDGET}: {found}"
    )


def test_budget_is_not_slack():
    found = _bypasses()
    assert len(found) >= IMAGE_REPO_BYPASS_BUDGET, (
        f"only {len(found)} bypass(es) remain but the budget is "
        f"{IMAGE_REPO_BYPASS_BUDGET}: lower IMAGE_REPO_BYPASS_BUDGET to "
        f"{len(found)} so the ratchet keeps biting."
    )


def test_the_scan_reads_the_shapes_it_claims_to():
    """The guard's own predicate, on synthetic sources.

    Run through ``_facade_only_imports`` rather than a paraphrase of it, so
    these cases exercise the scan the ratchet actually uses.
    """
    caught = _facade_only_imports(ast.parse(
        "from well_viewer.data.image_discovery import open_imgref_as_array\n"))
    assert [name for _, name in caught] == ["open_imgref_as_array"]

    # Aliased, and alongside a name that is not facade-only.
    caught = _facade_only_imports(ast.parse(
        "from well_viewer.data.image_discovery import ImgRef, "
        "find_well_images_and_masks as f\n"))
    assert [name for _, name in caught] == ["find_well_images_and_masks"]

    # The facade itself, and the grid loaders it deliberately does not wrap.
    for source in (
        "from well_viewer.data.image_repository import ImageRepository\n",
        "from well_viewer.data.frame_load import load_frame_array\n",
        "from well_viewer.data.image_discovery import ImgRef\n",
        "from elsewhere import open_imgref_as_array\n",
    ):
        assert _facade_only_imports(ast.parse(source)) == [], source
