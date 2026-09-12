"""The two image caches stay inside the modules that own them.

``zipfile_cache.GLOBAL_CACHE`` holds open ``ZipFile`` handles keyed by path and
mtime; ``image_discovery._DECODE_CACHE`` is the bounded LRU of decoded arrays.
Both are process-wide, both hand out **shared** objects — a decoded array is
handed to every caller that asks for the same image, and a zip handle is
serialised behind a mutex — and both are dropped together on a dataset swap
(``ImageRepository.invalidate``).

That makes reaching into either from outside its module the kind of coupling
that is invisible until it is wrong: a caller that mutates a shared array
corrupts every later reader of that image, and one that holds a handle across an
invalidation reads a replaced archive. Nothing does either today. This guard is
what keeps it that way — it is a lock on a property that already holds, not a
repair, and it is worth saying so plainly: watched against the base branch it
would pass, which is the honest status of a guard written to prevent a class of
defect rather than to fix one that occurred.

The names are matched as *attribute or import*, not as text, so a module that
imports ``GLOBAL_CACHE`` under another name is still caught.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._repo_scan import parse_source, python_sources, rel

ROOT = Path(__file__).resolve().parent.parent / "review"

#: The cache objects and the class behind one of them.
_PRIVATE_CACHES = frozenset({"GLOBAL_CACHE", "_DECODE_CACHE", "_DecodedArrayCache"})

#: Each cache's owning module — the only place its name may appear.
_OWNERS = {
    ROOT / "data" / "zipfile_cache.py",
    ROOT / "data" / "image_discovery.py",
}


def _cache_names_in(tree: ast.AST) -> set[str]:
    """Cache names imported or attribute-accessed anywhere in *tree*."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _PRIVATE_CACHES:
                    found.add(alias.name)
        elif isinstance(node, ast.Attribute) and node.attr in _PRIVATE_CACHES:
            found.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in _PRIVATE_CACHES:
            found.add(node.id)
    return found


def test_no_module_reaches_into_an_image_cache():
    offenders: list[str] = []
    for path in python_sources():
        if path in _OWNERS:
            continue
        try:
            tree = parse_source(path)
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        names = _cache_names_in(tree)
        if names:
            offenders.append(f"{rel(path)} ({', '.join(sorted(names))})")

    assert not offenders, (
        "these modules name an image cache that belongs to another module — go "
        "through ``ctx.state.images`` (or ``ImageRepository``) instead, so the "
        "shared arrays and zip handles keep one owner: " + "; ".join(offenders)
    )


def test_the_scan_reads_the_shapes_it_claims_to():
    """The predicate the guard uses, on synthetic sources."""
    assert _cache_names_in(ast.parse(
        "from review.data.zipfile_cache import GLOBAL_CACHE\n")) == {"GLOBAL_CACHE"}
    # Aliased on import — the text ``GLOBAL_CACHE`` still appears, and the
    # binding it creates does not, which is why the import is read too.
    assert _cache_names_in(ast.parse(
        "from review.data.zipfile_cache import GLOBAL_CACHE as c\n")) == {"GLOBAL_CACHE"}
    # Reached as an attribute of the module rather than imported.
    assert _cache_names_in(ast.parse(
        "import review.data.zipfile_cache as z\nz.GLOBAL_CACHE.invalidate()\n")
    ) == {"GLOBAL_CACHE"}
    assert _cache_names_in(ast.parse(
        "from review.data.image_discovery import open_imgref_as_array\n")) == set()
